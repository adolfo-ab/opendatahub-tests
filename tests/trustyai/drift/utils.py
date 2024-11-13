import json
import os
import subprocess
from typing import Any, Dict, List, Optional

import requests
from kubernetes.dynamic import DynamicClient
from kubernetes.dynamic.exceptions import NotFoundError
from ocp_resources.inference_service import InferenceService
from ocp_resources.namespace import Namespace
from ocp_resources.pod import Pod
from ocp_resources.route import Route
from ocp_resources.trustyai_service import TrustyAIService
from simple_logger.logger import get_logger
from timeout_sampler import TimeoutSampler

from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from tests.trustyai.constants import TIMEOUT_5MIN

LOGGER = get_logger(name=__name__)
TIMEOUT_30SEC: int = 30


def create_ocp_tooken(namespace: Namespace) -> str:
    return subprocess.check_output(["oc", "create", "token", "test-user", "-n", namespace.name]).decode().strip()


def send_request_to_trustyai_service(
    token: str,
    trustyai_service_route: TrustyAIService,
    endpoint: str,
    method: str,
    data: Optional[Dict[str, Any]] = None,
    json: Optional[Dict[str, Any]] = None,
) -> Any:
    url: str = f"https://{trustyai_service_route.host}{endpoint}"
    headers: Dict[str, str] = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    if method == "GET":
        return requests.get(url=url, headers=headers, verify=False)
    elif method == "POST":
        return requests.post(url=url, headers=headers, data=data, json=json, verify=False)
    raise ValueError(f"Unsupported HTTP method: {method}")


def get_trustyai_model_metadata(client: DynamicClient, token: str, trustyai_service: TrustyAIService) -> Any:
    trustyai_service_route = Route(
        client=client, namespace=trustyai_service.namespace, name="trustyai-service", ensure_exists=True
    )
    return send_request_to_trustyai_service(
        token=token,
        trustyai_service_route=trustyai_service_route,
        endpoint="/info",
        method="GET",
    )


def send_inference_request(
    token: str,
    inference_route: Route,
    data_batch: Any,
    file_path: str,
    max_retries: int = 5,
) -> None:
    """
    Send data batch to inference service with retry logic for network errors.

    Args:
        token: Authentication token
        inference_route: Route of the inference service
        data_batch: Data to be sent
        file_path: Path to the file being processed
        max_retries: Maximum number of retry attempts (default: 5)

    Returns:
        None

    Raises:
        RequestException: If all retry attempts fail
    """
    url: str = f"https://{inference_route.host}{inference_route.instance.spec.path}/infer"
    headers: Dict[str, str] = {"Authorization": f"Bearer {token}"}

    @retry(
        stop=stop_after_attempt(max_retries),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type(requests.RequestException),
        before_sleep=lambda retry_state: LOGGER.warning(
            f"Retry attempt {retry_state.attempt_number} for file {file_path} after error. "
            f"Waiting {retry_state.next_action.sleep} seconds..."
        ),
    )
    def _make_request() -> None:
        try:
            response: requests.Response = requests.post(
                url=url, headers=headers, data=data_batch, verify=False, timeout=TIMEOUT_30SEC
            )
            response.raise_for_status()
        except requests.RequestException as e:
            LOGGER.error(response.content)
            LOGGER.error(f"Error sending data for file: {file_path}. Error: {str(e)}")
            raise

    try:
        _make_request()
    except requests.RequestException:
        LOGGER.error(f"All {max_retries} retry attempts failed for file: {file_path}")
        raise


def get_trustyai_number_of_observations(client: DynamicClient, token: str, trustyai_service: TrustyAIService) -> int:
    model_metadata: requests.Response = get_trustyai_model_metadata(
        client=client, token=token, trustyai_service=trustyai_service
    )

    if not model_metadata:
        return 0

    try:
        metadata_json: Any = model_metadata.json()

        if not metadata_json:
            return 0

        model_key: str = next(iter(metadata_json))
        model = metadata_json.get(model_key)
        if not model:
            raise KeyError(f"Model data not found for key: {model_key}")

        if observations := model.get("data", {}).get("observations"):
            return observations

        raise KeyError("Observations data not found in model metadata")
    except Exception as e:
        raise TypeError(f"Failed to parse response: {str(e)}")


def wait_for_trustyai_to_register_inference_request(
    client: DynamicClient, token: str, trustyai_service: TrustyAIService, expected_observations: int
) -> None:
    current_observations: int = get_trustyai_number_of_observations(
        client=client, token=token, trustyai_service=trustyai_service
    )

    samples = TimeoutSampler(
        wait_timeout=TIMEOUT_30SEC,
        sleep=1,
        func=lambda: current_observations == expected_observations,
    )
    for sample in samples:
        if sample:
            return


def send_inference_requests_and_verify_trustyai_service(
    client: DynamicClient,
    token: str,
    data_path: str,
    trustyai_service: TrustyAIService,
    inference_service: InferenceService,
) -> None:
    """
    Sends all the data batches present in a given directory to an InferenceService, and verifies that TrustyAIService has registered the observations.

    Args:
        client (DynamicClient): The client instance for making API calls.
        token (str): Authentication token for API access.
        data_path (str): Directory path containing data batch files.
        trustyai_service (TrustyAIService): TrustyAIService that will register the model.
        inference_service (InferenceService): Model to be registered by TrustyAI.
    """
    inference_route: Route = Route(client=client, namespace=inference_service.namespace, name=inference_service.name)

    for root, _, files in os.walk(data_path):
        for file_name in files:
            file_path = os.path.join(root, file_name)

            with open(file_path, "r") as file:
                data = file.read()

            current_observations = get_trustyai_number_of_observations(
                client=client, token=token, trustyai_service=trustyai_service
            )
            send_inference_request(token=token, inference_route=inference_route, data_batch=data, file_path=file_path)
            wait_for_trustyai_to_register_inference_request(
                client=client,
                token=token,
                trustyai_service=trustyai_service,
                expected_observations=current_observations + json.loads(data)["inputs"][0]["shape"][0],
            )


def wait_for_modelmesh_pods_registered_by_trustyai(client: DynamicClient, namespace: Namespace) -> None:
    """
    Check if all the ModelMesh pods in a given namespace are ready and have been registered by the TrustyAIService in that same namespace.

    Args:
        client (DynamicClient): The client instance for interacting with the cluster.
        namespace (Namespace): The namespace where ModelMesh pods and TrustyAIService are deployed.
    """

    def _check_pods_ready_with_env() -> bool:
        modelmesh_pods: List[Pod] = [
            pod
            for pod in Pod.get(client=client, namespace=namespace)
            if pod.labels.get("modelmesh-service") == "modelmesh-serving"
        ]

        found_pod_with_env: bool = False

        for pod in modelmesh_pods:
            try:
                has_env_var = False
                # Check containers for environment variable
                for container in pod.instance.spec.containers:
                    if container.env is not None and any(env.name == "MM_PAYLOAD_PROCESSORS" for env in container.env):
                        has_env_var = True
                        found_pod_with_env = True
                        break

                # If pod has env var but isn't running, return False
                if has_env_var and pod.status != Pod.Status.RUNNING:
                    return False

            except NotFoundError:
                # Ignore pods that were deleted during the process
                continue

        # Return True only if we found at least one pod with the env var
        # and all pods with the env var are running
        return found_pod_with_env

    samples = TimeoutSampler(
        wait_timeout=TIMEOUT_5MIN,
        sleep=TIMEOUT_30SEC,
        func=_check_pods_ready_with_env,
    )
    for sample in samples:
        if sample:
            return
