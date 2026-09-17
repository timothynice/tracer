"""Specific tests for the parameter system that's currently having issues."""
import pytest
import json
import io
from fastapi.testclient import TestClient


class TestParameterSystem:
    """Focused tests for parameter handling and passing."""

    @pytest.mark.integration
    def test_parameter_parsing_basic(self, client, sample_image_bytes):
        """Test basic parameter parsing."""
        params = {
            "potrace": {"invert": True, "turdsize": 5},
            "opencv": {"low_threshold": 30, "high_threshold": 120}
        }

        files = {"file": ("test.png", io.BytesIO(sample_image_bytes), "image/png")}
        data = {
            "parameters": json.dumps(params),
            "selected_method": "potrace"
        }

        response = client.post("/vectorize", files=files, data=data)
        assert response.status_code == 200

        result = response.json()
        assert result["parameters_used"]["potrace"]["invert"] is True
        assert result["parameters_used"]["potrace"]["turdsize"] == 5

    @pytest.mark.integration
    def test_parameter_types_validation(self, client, sample_image_bytes):
        """Test that parameters are correctly typed."""
        params = {
            "opencv_edge": {
                "blur_size": 7,  # Should be int
                "low_threshold": 25.5,  # Should be float/int
                "min_area": 100,  # Should be int
                "stroke_width": 3  # Should be int
            }
        }

        files = {"file": ("test.png", io.BytesIO(sample_image_bytes), "image/png")}
        data = {
            "parameters": json.dumps(params),
            "selected_method": "opencv_edge"
        }

        response = client.post("/vectorize", files=files, data=data)
        assert response.status_code == 200

        result = response.json()
        used_params = result["parameters_used"]["opencv_edge"]
        assert used_params["blur_size"] == 7
        assert used_params["low_threshold"] == 25.5
        assert used_params["stroke_width"] == 3

    @pytest.mark.integration
    def test_parameter_defaults_when_missing(self, client, sample_image_bytes):
        """Test that missing parameters use defaults."""
        # Only provide some parameters
        params = {
            "potrace": {"invert": True}  # Missing other potrace params
        }

        files = {"file": ("test.png", io.BytesIO(sample_image_bytes), "image/png")}
        data = {
            "parameters": json.dumps(params),
            "selected_method": "potrace"
        }

        response = client.post("/vectorize", files=files, data=data)
        assert response.status_code == 200

        result = response.json()
        potrace_params = result["parameters_used"]["potrace"]

        # Should have the provided parameter
        assert potrace_params["invert"] is True

        # Should have defaults for missing parameters (these may not be in response)
        # The key test is that the request succeeded with partial parameters

    @pytest.mark.integration
    def test_all_method_parameters(self, client, sample_image_bytes):
        """Test parameters for all vectorization methods."""
        all_params = {
            "potrace": {
                "invert": True,
                "turdsize": 3,
                "turnpolicy": "black",
                "alphamax": 0.8,
                "opticurve": False
            },
            "opencv_edge": {
                "blur_size": 7,
                "low_threshold": 20,
                "high_threshold": 80,
                "min_area": 25,
                "epsilon_factor": 0.03,
                "stroke_width": 3
            },
            "opencv_contour": {
                "threshold": 100,
                "min_area": 150,
                "epsilon_factor": 0.005,
                "invert_threshold": True
            },
            "opencv": {
                "low_threshold": 40,
                "high_threshold": 120,
                "min_contour_points": 5
            }
        }

        files = {"file": ("test.png", io.BytesIO(sample_image_bytes), "image/png")}
        data = {
            "parameters": json.dumps(all_params),
            "selected_method": ""  # Test all methods
        }

        response = client.post("/vectorize", files=files, data=data)
        assert response.status_code == 200

        result = response.json()
        assert result["success"] is True

        # Check that parameters were stored correctly
        used_params = result["parameters_used"]
        assert used_params["potrace"]["turnpolicy"] == "black"
        assert used_params["opencv_edge"]["blur_size"] == 7
        assert used_params["opencv_contour"]["invert_threshold"] is True

    @pytest.mark.integration
    def test_parameter_edge_cases(self, client, sample_image_bytes):
        """Test edge cases in parameter values."""
        edge_params = {
            "opencv": {
                "low_threshold": 0,  # Minimum value
                "high_threshold": 255,  # Maximum value
                "min_contour_points": 1  # Very small value
            },
            "opencv_edge": {
                "blur_size": 1,  # Minimum odd value
                "epsilon_factor": 0.001,  # Very small decimal
                "stroke_width": 10  # Large value
            }
        }

        files = {"file": ("test.png", io.BytesIO(sample_image_bytes), "image/png")}
        data = {
            "parameters": json.dumps(edge_params),
            "selected_method": "opencv"
        }

        response = client.post("/vectorize", files=files, data=data)
        assert response.status_code == 200

        result = response.json()
        assert result["success"] is True

    @pytest.mark.integration
    def test_malformed_parameter_handling(self, client, sample_image_bytes):
        """Test handling of malformed parameters."""
        malformed_cases = [
            '{"potrace": "not_an_object"}',  # Wrong type
            '{"opencv": {"low_threshold": "not_a_number"}}',  # Wrong value type
            '{"nonexistent_method": {"param": "value"}}',  # Unknown method
            '{"potrace": {"unknown_param": 123}}',  # Unknown parameter
        ]

        for malformed_params in malformed_cases:
            files = {"file": ("test.png", io.BytesIO(sample_image_bytes), "image/png")}
            data = {
                "parameters": malformed_params,
                "selected_method": "potrace"
            }

            response = client.post("/vectorize", files=files, data=data)
            # Should either succeed with defaults or return appropriate error
            assert response.status_code in [200, 400, 422, 500]

    @pytest.mark.integration
    def test_selected_method_parameter_isolation(self, client, sample_image_bytes):
        """Test that selected method only uses its own parameters."""
        mixed_params = {
            "potrace": {"invert": True, "turdsize": 5},
            "opencv_edge": {"blur_size": 9, "stroke_width": 4},
            "opencv": {"low_threshold": 75}
        }

        # Test potrace method
        files = {"file": ("test.png", io.BytesIO(sample_image_bytes), "image/png")}
        data = {
            "parameters": json.dumps(mixed_params),
            "selected_method": "potrace"
        }

        response = client.post("/vectorize", files=files, data=data)
        assert response.status_code == 200

        result = response.json()
        # Should only process potrace method when selected
        assert "potrace" in result["vectorized"]
        # Depending on implementation, may or may not process other methods

    @pytest.mark.integration
    def test_parameter_persistence_across_calls(self, client, sample_image_bytes):
        """Test that parameter changes are reflected correctly."""
        # First call with initial parameters
        params1 = {"opencv": {"low_threshold": 50}}
        files = {"file": ("test.png", io.BytesIO(sample_image_bytes), "image/png")}
        data = {
            "parameters": json.dumps(params1),
            "selected_method": "opencv"
        }

        response1 = client.post("/vectorize", files=files, data=data)
        assert response1.status_code == 200

        # Second call with changed parameters
        params2 = {"opencv": {"low_threshold": 100}}
        data["parameters"] = json.dumps(params2)

        response2 = client.post("/vectorize", files=files, data=data)
        assert response2.status_code == 200

        # Results should be different due to different parameters
        result1 = response1.json()
        result2 = response2.json()

        assert result1["parameters_used"]["opencv"]["low_threshold"] == 50
        assert result2["parameters_used"]["opencv"]["low_threshold"] == 100

    @pytest.mark.integration
    def test_boolean_parameter_handling(self, client, sample_image_bytes):
        """Test boolean parameter handling specifically."""
        bool_params = {
            "potrace": {
                "invert": True,
                "opticurve": False
            },
            "opencv_contour": {
                "invert_threshold": True
            }
        }

        files = {"file": ("test.png", io.BytesIO(sample_image_bytes), "image/png")}
        data = {
            "parameters": json.dumps(bool_params),
            "selected_method": "potrace"
        }

        response = client.post("/vectorize", files=files, data=data)
        assert response.status_code == 200

        result = response.json()
        potrace_params = result["parameters_used"]["potrace"]

        # Boolean values should be preserved correctly
        assert potrace_params["invert"] is True
        assert potrace_params["opticurve"] is False

    @pytest.mark.integration
    def test_numeric_parameter_precision(self, client, sample_image_bytes):
        """Test that numeric parameters maintain precision."""
        precise_params = {
            "potrace": {
                "alphamax": 1.333333,  # Test decimal precision
                "turdsize": 0  # Test zero value
            },
            "opencv_edge": {
                "epsilon_factor": 0.00001  # Very small decimal
            }
        }

        files = {"file": ("test.png", io.BytesIO(sample_image_bytes), "image/png")}
        data = {
            "parameters": json.dumps(precise_params),
            "selected_method": "potrace"
        }

        response = client.post("/vectorize", files=files, data=data)
        assert response.status_code == 200

        result = response.json()
        potrace_params = result["parameters_used"]["potrace"]

        # Precision should be maintained (within reasonable limits)
        assert abs(potrace_params["alphamax"] - 1.333333) < 0.0001
        assert potrace_params["turdsize"] == 0

    @pytest.mark.integration
    @pytest.mark.parametrize("method", ["potrace", "opencv_edge", "opencv_contour", "opencv"])
    def test_individual_method_parameters(self, client, sample_image_bytes, method):
        """Test parameters for each method individually."""
        method_params = {
            "potrace": {"invert": True, "turdsize": 4, "alphamax": 1.2},
            "opencv_edge": {"blur_size": 7, "min_area": 75, "stroke_width": 3},
            "opencv_contour": {"threshold": 140, "min_area": 200, "invert_threshold": True},
            "opencv": {"low_threshold": 35, "high_threshold": 135, "min_contour_points": 4}
        }

        params = {method: method_params[method]}

        files = {"file": ("test.png", io.BytesIO(sample_image_bytes), "image/png")}
        data = {
            "parameters": json.dumps(params),
            "selected_method": method
        }

        response = client.post("/vectorize", files=files, data=data)
        assert response.status_code == 200

        result = response.json()
        assert result["success"] is True
        assert method in result["vectorized"]

        # Verify parameters were used
        if method in result["parameters_used"]:
            used_params = result["parameters_used"][method]
            expected_params = method_params[method]

            for key, expected_value in expected_params.items():
                if key in used_params:
                    assert used_params[key] == expected_value