from time import sleep
from typing import Set

from kubernetes.dynamic import DynamicClient
from ocp_resources.lm_eval_job import LMEvalJob
from ocp_resources.pod import Pod

from timeout_sampler import TimeoutWatch

from tests.trustyai.constants import TIMEOUT_10MIN
from utilities.infra import TIMEOUT_2MIN


def verify_lmevaljob_running(client: DynamicClient, lmevaljob: LMEvalJob) -> None:
    """
    Verifies that an LMEvalJob Pod reaches Running state and maintains Running/Succeeded state.
    Waits for Pod to enter Running state, then checks it stays Running or Succeeded for 2 minutes.

    Args:
        client: DynamicClient instance for interacting with Kubernetes
        lmevaljob: LMEvalJob object representing the job to verify

    Raises:
        TimeoutError: If Pod doesn't reach Running state within 10 minutes
        AssertionError: If Pod doesn't stay in one of the desired states for 2 minutes
    """

    lmevaljob_pod = Pod(client=client, name=lmevaljob.name, namespace=lmevaljob.namespace, wait_for_resource=True)
    lmevaljob_pod.wait_for_status(status=lmevaljob_pod.Status.RUNNING, timeout=TIMEOUT_10MIN)

    check_pod_status_in_time(pod=lmevaljob_pod, status={Pod.Status.RUNNING, Pod.Status.SUCCEEDED})


def check_pod_status_in_time(pod: Pod, status: Set[Pod.Status], duration: int = TIMEOUT_2MIN, wait: int = 1) -> None:
    """
    Checks if a pod has a given status for a given duration.

    Args:
        pod (Pod): The pod to check
        status (Set[Pod.Status]): Expected pod status(es)
        duration (int): Maximum time to check for in seconds
        wait (int): Time to wait between checks in seconds

    Raises:
        AssertionError: If pod status is not in the expected set
    """

    _start = TimeoutWatch(timeout=duration)
    while _start.remaining_time() > 0:
        assert pod.status in status
        sleep(wait)  # noqa: FCN001
