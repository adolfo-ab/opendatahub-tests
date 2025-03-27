
class TestTrustyAIOperatorImages:
    def test_trustyai_operator_images(self, configmap, trustyai_operator_pod):
        # assert that configmap image == trustyai_operator_pod image

    def test_trustyai_service_images(self, configmap, trustyai_service_with_pvc_storage):
        # assert that configmap image == trustyai_service_pod image
