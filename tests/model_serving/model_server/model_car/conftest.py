from typing import Any, Generator

import pytest
from _pytest.fixtures import FixtureRequest
from kubernetes.dynamic import DynamicClient
from ocp_resources.inference_service import InferenceService
from ocp_resources.namespace import Namespace
from ocp_resources.serving_runtime import ServingRuntime

from utilities.constants import KServeDeploymentType
from utilities.infra import create_isvc


@pytest.fixture(scope="class")
def model_car_tgis_inference_service(
    request: FixtureRequest,
    admin_client: DynamicClient,
    model_namespace: Namespace,
    serving_runtime_from_template: ServingRuntime,
) -> Generator[InferenceService, Any, Any]:
    with create_isvc(
        client=admin_client,
        name="tgis-model-car",
        namespace=model_namespace.name,
        runtime=serving_runtime_from_template.name,
        storage_uri=request.param["storage-uri"],
        model_format=serving_runtime_from_template.instance.spec.supportedModelFormats[0].name,
        deployment_mode=KServeDeploymentType.SERVERLESS,
        wait_for_predictor_pods=False,  # Until modelcar initContainer completed, other containers may have Error status
    ) as isvc:
        yield isvc
