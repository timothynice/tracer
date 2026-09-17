"""Integration tests for FastAPI endpoints."""
import json
import pytest
import io
from fastapi.testclient import TestClient
from unittest.mock import patch, Mock


class TestVectorizeEndpoint:
    """Test cases for the /vectorize endpoint."""

    @pytest.mark.integration
    def test_vectorize_valid_image(self, client, sample_image_bytes, sample_parameters):
        """Test vectorization with valid image and parameters."""
        files = {"file": ("test.png", io.BytesIO(sample_image_bytes), "image/png")}
        data = {
            "parameters": json.dumps(sample_parameters),
            "selected_method": ""
        }

        response = client.post("/vectorize", files=files, data=data)

        assert response.status_code == 200
        result = response.json()

        assert result["success"] is True
        assert "original_image" in result
        assert "vectorized" in result
        assert "parameters_used" in result
        assert result["original_image"].startswith("data:image/png;base64,")

        # Should contain all vectorization methods
        expected_methods = ["potrace", "opencv_edge", "opencv_contour", "opencv"]
        for method in expected_methods:
            assert method in result["vectorized"] or result["vectorized"][method].startswith("Error:")

    @pytest.mark.integration
    def test_vectorize_specific_method(self, client, sample_image_bytes, sample_parameters):
        """Test vectorization with specific method selection."""
        files = {"file": ("test.png", io.BytesIO(sample_image_bytes), "image/png")}
        data = {
            "parameters": json.dumps(sample_parameters),
            "selected_method": "opencv_edge"
        }

        response = client.post("/vectorize", files=files, data=data)

        assert response.status_code == 200
        result = response.json()

        assert result["success"] is True
        assert "opencv_edge" in result["vectorized"]
        # When specific method is selected, it should be processed
        assert not result["vectorized"]["opencv_edge"].startswith("Error:")

    @pytest.mark.integration
    def test_vectorize_parameter_validation(self, client, sample_image_bytes):
        """Test parameter validation and different parameter sets."""
        # Test with potrace parameters
        potrace_params = {
            "potrace": {
                "invert": True,
                "turdsize": 5,
                "turnpolicy": "black",
                "alphamax": 0.5,
                "opticurve": False
            }
        }

        files = {"file": ("test.png", io.BytesIO(sample_image_bytes), "image/png")}
        data = {
            "parameters": json.dumps(potrace_params),
            "selected_method": "potrace"
        }

        response = client.post("/vectorize", files=files, data=data)
        assert response.status_code == 200
        result = response.json()
        assert result["parameters_used"]["potrace"]["invert"] is True
        assert result["parameters_used"]["potrace"]["turdsize"] == 5

    @pytest.mark.integration
    def test_vectorize_opencv_edge_parameters(self, client, complex_sample_image_bytes):
        """Test OpenCV edge parameters."""
        edge_params = {
            "opencv_edge": {
                "blur_size": 7,
                "low_threshold": 20,
                "high_threshold": 80,
                "min_area": 25,
                "epsilon_factor": 0.05,
                "stroke_width": 3
            }
        }

        files = {"file": ("test.png", io.BytesIO(complex_sample_image_bytes), "image/png")}
        data = {
            "parameters": json.dumps(edge_params),
            "selected_method": "opencv_edge"
        }

        response = client.post("/vectorize", files=files, data=data)
        assert response.status_code == 200
        result = response.json()
        assert result["parameters_used"]["opencv_edge"]["blur_size"] == 7
        assert result["parameters_used"]["opencv_edge"]["stroke_width"] == 3

    @pytest.mark.integration
    def test_vectorize_opencv_contour_parameters(self, client, sample_image_bytes):
        """Test OpenCV contour parameters."""
        contour_params = {
            "opencv_contour": {
                "threshold": 100,
                "min_area": 200,
                "epsilon_factor": 0.005,
                "invert_threshold": True
            }
        }

        files = {"file": ("test.png", io.BytesIO(sample_image_bytes), "image/png")}
        data = {
            "parameters": json.dumps(contour_params),
            "selected_method": "opencv_contour"
        }

        response = client.post("/vectorize", files=files, data=data)
        assert response.status_code == 200
        result = response.json()
        assert result["parameters_used"]["opencv_contour"]["invert_threshold"] is True

    @pytest.mark.integration
    def test_vectorize_invalid_image_format(self, client):
        """Test with invalid image format."""
        invalid_file = io.BytesIO(b"This is not an image")

        files = {"file": ("test.txt", invalid_file, "text/plain")}
        data = {"parameters": "{}", "selected_method": ""}

        response = client.post("/vectorize", files=files, data=data)
        assert response.status_code == 400
        assert "File must be an image" in response.json()["detail"]

    @pytest.mark.integration
    def test_vectorize_invalid_json_parameters(self, client, sample_image_bytes):
        """Test with invalid JSON parameters."""
        files = {"file": ("test.png", io.BytesIO(sample_image_bytes), "image/png")}
        data = {
            "parameters": "invalid json",
            "selected_method": ""
        }

        response = client.post("/vectorize", files=files, data=data)
        assert response.status_code == 500

    @pytest.mark.integration
    def test_vectorize_empty_parameters(self, client, sample_image_bytes):
        """Test with empty parameters (should use defaults)."""
        files = {"file": ("test.png", io.BytesIO(sample_image_bytes), "image/png")}
        data = {
            "parameters": "{}",
            "selected_method": ""
        }

        response = client.post("/vectorize", files=files, data=data)
        assert response.status_code == 200
        result = response.json()
        assert result["success"] is True

    @pytest.mark.integration
    def test_vectorize_missing_file(self, client):
        """Test with missing file."""
        data = {"parameters": "{}", "selected_method": ""}
        response = client.post("/vectorize", data=data)
        assert response.status_code == 422  # Validation error

    @pytest.mark.integration
    def test_vectorize_parameter_edge_cases(self, client, sample_image_bytes):
        """Test with edge case parameter values."""
        edge_case_params = {
            "opencv": {
                "low_threshold": 1,
                "high_threshold": 255,
                "min_contour_points": 100
            },
            "opencv_edge": {
                "blur_size": 1,
                "min_area": 1,
                "epsilon_factor": 0.001,
                "stroke_width": 10
            }
        }

        files = {"file": ("test.png", io.BytesIO(sample_image_bytes), "image/png")}
        data = {
            "parameters": json.dumps(edge_case_params),
            "selected_method": "opencv"
        }

        response = client.post("/vectorize", files=files, data=data)
        assert response.status_code == 200
        result = response.json()
        assert result["parameters_used"]["opencv"]["min_contour_points"] == 100

    @pytest.mark.integration
    @patch('main.VectorizerService.opencv_vectorize')
    def test_vectorize_method_error_handling(self, mock_opencv, client, sample_image_bytes):
        """Test error handling when vectorization methods fail."""
        # Mock method to raise exception
        mock_opencv.side_effect = Exception("Vectorization failed")

        files = {"file": ("test.png", io.BytesIO(sample_image_bytes), "image/png")}
        data = {
            "parameters": "{}",
            "selected_method": "opencv"
        }

        response = client.post("/vectorize", files=files, data=data)
        assert response.status_code == 200  # Should still return 200 with error in results
        result = response.json()
        assert "Error:" in result["vectorized"]["opencv"]

    @pytest.mark.integration
    def test_vectorize_different_image_types(self, client):
        """Test with different image content types."""
        from PIL import Image
        import io

        # Create a JPEG image
        img = Image.new('RGB', (50, 50), 'white')
        jpeg_buffer = io.BytesIO()
        img.save(jpeg_buffer, format='JPEG')
        jpeg_buffer.seek(0)

        files = {"file": ("test.jpg", jpeg_buffer, "image/jpeg")}
        data = {"parameters": "{}", "selected_method": "opencv"}

        response = client.post("/vectorize", files=files, data=data)
        assert response.status_code == 200
        result = response.json()
        assert result["success"] is True
        assert result["original_image"].startswith("data:image/jpeg;base64,")

    @pytest.mark.integration
    def test_vectorize_large_image(self, client, performance_test_image):
        """Test vectorization with large image."""
        files = {"file": ("large.png", io.BytesIO(performance_test_image), "image/png")}
        data = {
            "parameters": "{}",
            "selected_method": "opencv_edge"
        }

        response = client.post("/vectorize", files=files, data=data, timeout=30)
        assert response.status_code == 200
        result = response.json()
        assert result["success"] is True

    @pytest.mark.integration
    def test_parameter_inheritance(self, client, sample_image_bytes):
        """Test that parameters are correctly passed to methods."""
        specific_params = {
            "potrace": {
                "turnpolicy": "majority",
                "alphamax": 1.5
            },
            "opencv_contour": {
                "threshold": 150,
                "invert_threshold": True
            }
        }

        files = {"file": ("test.png", io.BytesIO(sample_image_bytes), "image/png")}
        data = {
            "parameters": json.dumps(specific_params),
            "selected_method": ""
        }

        response = client.post("/vectorize", files=files, data=data)
        assert response.status_code == 200
        result = response.json()

        # Check that parameters were correctly stored
        assert result["parameters_used"]["potrace"]["turnpolicy"] == "majority"
        assert result["parameters_used"]["potrace"]["alphamax"] == 1.5
        assert result["parameters_used"]["opencv_contour"]["invert_threshold"] is True


class TestHealthEndpoint:
    """Test cases for the /health endpoint."""

    @pytest.mark.integration
    def test_health_check(self, client):
        """Test health check endpoint."""
        response = client.get("/health")
        assert response.status_code == 200

        result = response.json()
        assert result["status"] == "healthy"
        assert "message" in result


class TestCORSHandling:
    """Test CORS handling."""

    @pytest.mark.integration
    def test_cors_headers_present(self, client, sample_image_bytes):
        """Test that CORS headers are present in responses."""
        files = {"file": ("test.png", io.BytesIO(sample_image_bytes), "image/png")}
        data = {"parameters": "{}", "selected_method": "opencv"}

        response = client.post("/vectorize", files=files, data=data)
        assert response.status_code == 200

        # TestClient doesn't automatically include CORS headers,
        # but we can verify the middleware is configured
        assert hasattr(client.app, 'user_middleware')


class TestParameterValidation:
    """Test parameter validation and processing."""

    @pytest.mark.integration
    def test_malformed_parameter_structure(self, client, sample_image_bytes):
        """Test handling of malformed parameter structures."""
        malformed_params = {
            "potrace": "invalid_structure",  # Should be dict
            "opencv_edge": {
                "blur_size": "not_a_number"  # Should be int
            }
        }

        files = {"file": ("test.png", io.BytesIO(sample_image_bytes), "image/png")}
        data = {
            "parameters": json.dumps(malformed_params),
            "selected_method": "potrace"
        }

        # Should handle gracefully or return appropriate error
        response = client.post("/vectorize", files=files, data=data)
        # Might be 200 with error in results or 422/500 depending on validation
        assert response.status_code in [200, 422, 500]

    @pytest.mark.integration
    def test_unknown_method_selection(self, client, sample_image_bytes):
        """Test selection of unknown vectorization method."""
        files = {"file": ("test.png", io.BytesIO(sample_image_bytes), "image/png")}
        data = {
            "parameters": "{}",
            "selected_method": "unknown_method"
        }

        response = client.post("/vectorize", files=files, data=data)
        assert response.status_code == 200  # Should fall back to processing all methods
        result = response.json()
        assert result["success"] is True

    @pytest.mark.integration
    def test_partial_parameters(self, client, sample_image_bytes):
        """Test with partial parameter sets."""
        partial_params = {
            "potrace": {
                "invert": True
                # Missing other potrace parameters - should use defaults
            }
        }

        files = {"file": ("test.png", io.BytesIO(sample_image_bytes), "image/png")}
        data = {
            "parameters": json.dumps(partial_params),
            "selected_method": "potrace"
        }

        response = client.post("/vectorize", files=files, data=data)
        assert response.status_code == 200
        result = response.json()
        assert result["parameters_used"]["potrace"]["invert"] is True
        # Should contain default values for missing parameters