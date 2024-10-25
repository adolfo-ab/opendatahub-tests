import subprocess

import pytest
import yaml
from kubernetes.dynamic import DynamicClient
from kubernetes.dynamic.exceptions import ConflictError
from ocp_resources.config_map import ConfigMap
from ocp_resources.deployment import Deployment
from ocp_resources.namespace import Namespace
from ocp_resources.pod import Pod
from ocp_resources.secret import Secret
from ocp_resources.service import Service
from ocp_resources.service_account import ServiceAccount
from ocp_resources.trustyai_service import TrustyAIService

from tests.trustyai.constants import TRUSTYAI_SERVICE

MINIO: str = "minio"
OPENDATAHUB_IO: str = "opendatahub.io"


@pytest.fixture(scope="class")
def trustyai_service_pvc(
    admin_client: DynamicClient,
    model_namespace: Namespace,
    modelmesh_serviceaccount: ServiceAccount,
    cluster_monitoring_config: ConfigMap,
    user_workload_monitoring_config: ConfigMap,
) -> TrustyAIService:
    with TrustyAIService(
        client=admin_client,
        name=TRUSTYAI_SERVICE,
        namespace=model_namespace.name,
        storage={"format": "PVC", "folder": "/inputs", "size": "1Gi"},
        data={"filename": "data.csv", "format": "CSV"},
        metrics={"schedule": "5s"},
    ) as trustyai_service:
        trustyai_deployment = Deployment(namespace=model_namespace.name, name=TRUSTYAI_SERVICE, wait_for_resource=True)
        trustyai_deployment.wait_for_replicas()
        yield trustyai_service


@pytest.fixture(scope="class")
def openshift_token(model_namespace):
    return subprocess.check_output(["oc", "whoami", "-t", model_namespace.name]).decode().strip()


@pytest.fixture(scope="class")
def modelmesh_serviceaccount(admin_client: DynamicClient, model_namespace: Namespace) -> ServiceAccount:
    with ServiceAccount(client=admin_client, name="modelmesh-serving-sa", namespace=model_namespace.name) as sa:
        yield sa


@pytest.fixture(scope="session")
def cluster_monitoring_config(admin_client: DynamicClient) -> ConfigMap:
    config_yaml = yaml.dump({"enableUserWorkload": "true"})
    name = "cluster-monitoring-config"
    namespace = "openshift-monitoring"
    try:
        with ConfigMap(
            client=admin_client,
            name=name,
            namespace=namespace,
            data={"config.yaml": config_yaml},
        ) as cm:
            yield cm
    except (
        ConflictError
    ):  # This resource is usually created when doing exploratory testing, add this exception for convenience
        yield ConfigMap(name=name, namespace=namespace)


@pytest.fixture(scope="session")
def user_workload_monitoring_config(admin_client: DynamicClient) -> ConfigMap:
    config_yaml = yaml.dump({"prometheus": {"logLevel": "debug", "retention": "15d"}})
    name = "user-workload-monitoring-config"
    namespace = "openshift-user-workload-monitoring"
    try:
        with ConfigMap(
            client=admin_client,
            name=name,
            namespace=namespace,
            data={"config.yaml": config_yaml},
        ) as cm:
            yield cm
    except (
        ConflictError
    ):  # This resource is usually created when doing exploratory testing, add this exception for convenience
        yield ConfigMap(name=name, namespace=namespace)


@pytest.fixture(scope="class")
def minio_pod(admin_client: DynamicClient, model_namespace: Namespace) -> Pod:
    with Pod(
        client=admin_client,
        name=MINIO,
        namespace=model_namespace.name,
        containers=[
            {
                "args": [
                    "server",
                    "/data1",
                ],
                "env": [
                    {
                        "name": "MINIO_ACCESS_KEY",
                        "value": "THEACCESSKEY",
                    },
                    {
                        "name": "MINIO_SECRET_KEY",
                        "value": "THESECRETKEY",
                    },
                ],
                "image": "quay.io/trustyai/modelmesh-minio-examples@"
                "sha256:e8360ec33837b347c76d2ea45cd4fea0b40209f77520181b15e534b101b1f323",
                "name": MINIO,
            }
        ],
        label={"app": "minio", "maistra.io/expose-route": "true"},
        annotations={"sidecar.istio.io/inject": "true"},
    ) as minio_pod:
        yield minio_pod


@pytest.fixture(scope="class")
def minio_service(admin_client: DynamicClient, model_namespace: Namespace) -> Service:
    with Service(
        client=admin_client,
        name=MINIO,
        namespace=model_namespace.name,
        ports=[
            {
                "name": "minio-client-port",
                "port": 9000,
                "protocol": "TCP",
                "targetPort": 9000,
            }
        ],
        selector={
            "app": MINIO,
        },
    ) as minio_service:
        yield minio_service


@pytest.fixture(scope="class")
def minio_data_connection(
    admin_client: DynamicClient, model_namespace: Namespace, minio_pod: Pod, minio_service: Service
) -> Secret:
    with Secret(
        client=admin_client,
        name="aws-connection-minio-data-connection",
        namespace=model_namespace.name,
        data_dict={
            "AWS_ACCESS_KEY_ID": "VEhFQUNDRVNTS0VZ",
            "AWS_DEFAULT_REGION": "dXMtc291dGg=",
            "AWS_S3_BUCKET": "bW9kZWxtZXNoLWV4YW1wbGUtbW9kZWxz",
            "AWS_S3_ENDPOINT": "aHR0cDovL21pbmlvOjkwMDA=",
            "AWS_SECRET_ACCESS_KEY": "VEhFU0VDUkVUS0VZ",  # pragma: allowlist secret
        },
        label={
            f"{OPENDATAHUB_IO}/dashboard": "true",
            f"{OPENDATAHUB_IO}/managed": "true",
        },
        annotations={
            f"{OPENDATAHUB_IO}/connection-type": "s3",
            "openshift.io/display-name": "Minio Data Connection",
        },
    ) as minio_secret:
        yield minio_secret
