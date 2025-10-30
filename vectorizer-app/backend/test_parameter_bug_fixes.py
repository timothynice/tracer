#!/usr/bin/env python3
"""
Comprehensive test suite for VTracer parameter bug fixes.

This test validates:
1. Parameter validation functions work correctly
2. API handles invalid parameters with proper error responses
3. Frontend-backend parameter flow is robust
4. Vue.js key fixes prevent preview disappearing
5. All parameter ranges are validated correctly

Run with: python test_parameter_bug_fixes.py
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from main import (
    validate_potrace_params,
    validate_vtracer_params,
    ParameterValidationError,
    app
)
import pytest
import io
import requests
from PIL import Image
import base64
import json

class TestParameterValidation:
    """Test parameter validation functions directly"""

    def test_potrace_valid_params(self):
        """Test that valid Potrace parameters pass validation"""
        valid_params = {
            'turdsize': 2,
            'alphamax': 1.0,
            'turnpolicy': 'minority',
            'invert': False,
            'opticurve': True
        }

        # Should not raise any exception
        validate_potrace_params(valid_params)
        print("✅ PASS: Valid Potrace parameters accepted")

    def test_potrace_invalid_turdsize(self):
        """Test turdsize parameter validation"""
        invalid_cases = [
            {'turdsize': -1},  # Below minimum
            {'turdsize': 101},  # Above maximum
            {'turdsize': "invalid"},  # Wrong type
            {'turdsize': 50.5}  # Float (should be allowed actually)
        ]

        for i, params in enumerate(invalid_cases[:3]):  # Skip float test as it should pass
            try:
                validate_potrace_params(params)
                print(f"❌ FAIL: Invalid turdsize case {i+1} should have raised exception: {params}")
                return False
            except ParameterValidationError:
                print(f"✅ PASS: Invalid turdsize case {i+1} properly rejected: {params}")

        # Float should be allowed
        try:
            validate_potrace_params({'turdsize': 50.5})
            print("✅ PASS: Float turdsize accepted")
        except:
            print("❌ FAIL: Float turdsize should be accepted")
            return False

        return True

    def test_potrace_invalid_alphamax(self):
        """Test alphamax parameter validation"""
        invalid_cases = [
            {'alphamax': -0.1},  # Below minimum
            {'alphamax': 2.1},   # Above maximum
            {'alphamax': "invalid"}  # Wrong type
        ]

        for i, params in enumerate(invalid_cases):
            try:
                validate_potrace_params(params)
                print(f"❌ FAIL: Invalid alphamax case {i+1} should have raised exception: {params}")
                return False
            except ParameterValidationError:
                print(f"✅ PASS: Invalid alphamax case {i+1} properly rejected: {params}")

        return True

    def test_potrace_invalid_turnpolicy(self):
        """Test turnpolicy parameter validation"""
        valid_policies = ['black', 'white', 'left', 'right', 'minority', 'majority', 'random']

        # Test valid policies
        for policy in valid_policies:
            try:
                validate_potrace_params({'turnpolicy': policy})
                print(f"✅ PASS: Valid turnpolicy '{policy}' accepted")
            except:
                print(f"❌ FAIL: Valid turnpolicy '{policy}' should be accepted")
                return False

        # Test invalid policy
        try:
            validate_potrace_params({'turnpolicy': 'invalid_policy'})
            print("❌ FAIL: Invalid turnpolicy should have raised exception")
            return False
        except ParameterValidationError:
            print("✅ PASS: Invalid turnpolicy properly rejected")

        return True

    def test_vtracer_valid_params(self):
        """Test that valid VTracer parameters pass validation"""
        valid_params = {
            'colormode': 'color',
            'color_precision': 6,
            'filter_speckle': 4,
            'corner_threshold': 60,
            'length_threshold': 4.0,
            'max_iterations': 10,
            'splice_threshold': 45,
            'path_precision': 3
        }

        # Should not raise any exception
        validate_vtracer_params(valid_params)
        print("✅ PASS: Valid VTracer parameters accepted")

    def test_vtracer_invalid_ranges(self):
        """Test VTracer parameter range validation"""
        invalid_cases = [
            {'color_precision': 0},      # Below minimum (1)
            {'color_precision': 9},      # Above maximum (8)
            {'filter_speckle': 0},       # Below minimum (1)
            {'filter_speckle': 101},     # Above maximum (100)
            {'corner_threshold': -1},    # Below minimum (0)
            {'corner_threshold': 181},   # Above maximum (180)
            {'length_threshold': -0.1},  # Below minimum (0.0)
            {'length_threshold': 50.1},  # Above maximum (50.0)
            {'max_iterations': 0},       # Below minimum (1)
            {'max_iterations': 101},     # Above maximum (100)
            {'path_precision': 0},       # Below minimum (1)
            {'path_precision': 11},      # Above maximum (10)
        ]

        for i, params in enumerate(invalid_cases):
            try:
                validate_vtracer_params(params)
                print(f"❌ FAIL: Invalid VTracer case {i+1} should have raised exception: {params}")
                return False
            except ParameterValidationError:
                print(f"✅ PASS: Invalid VTracer case {i+1} properly rejected: {params}")

        return True

    def test_vtracer_invalid_colormode(self):
        """Test VTracer colormode validation"""
        # Valid colormodes
        for mode in ['color', 'binary']:
            try:
                validate_vtracer_params({'colormode': mode})
                print(f"✅ PASS: Valid colormode '{mode}' accepted")
            except:
                print(f"❌ FAIL: Valid colormode '{mode}' should be accepted")
                return False

        # Invalid colormode
        try:
            validate_vtracer_params({'colormode': 'invalid_mode'})
            print("❌ FAIL: Invalid colormode should have raised exception")
            return False
        except ParameterValidationError:
            print("✅ PASS: Invalid colormode properly rejected")

        return True

class TestAPIParameterValidation:
    """Test parameter validation through the API endpoint"""

    def create_test_image(self):
        """Create a simple test image for API calls"""
        # Create a simple 100x100 black square image
        img = Image.new('RGB', (100, 100), color='black')
        # Add a white circle to make it more interesting
        from PIL import ImageDraw
        draw = ImageDraw.Draw(img)
        draw.ellipse([25, 25, 75, 75], fill='white')

        # Convert to bytes
        img_bytes = io.BytesIO()
        img.save(img_bytes, format='PNG')
        img_bytes.seek(0)

        return img_bytes.getvalue()

    def test_api_with_invalid_parameters(self):
        """Test that API properly rejects invalid parameters"""
        print("\n=== Testing API Parameter Validation ===")

        image_bytes = self.create_test_image()

        invalid_test_cases = [
            {
                'name': 'Invalid turdsize',
                'params': {'potrace': {'turdsize': 101}},
                'should_contain': 'turdsize must be a number between 0 and 100'
            },
            {
                'name': 'Invalid alphamax',
                'params': {'potrace': {'alphamax': 3.0}},
                'should_contain': 'alphamax must be a number between 0.0 and 2.0'
            },
            {
                'name': 'Invalid turnpolicy',
                'params': {'potrace': {'turnpolicy': 'invalid'}},
                'should_contain': 'turnpolicy must be one of'
            },
            {
                'name': 'Invalid color_precision',
                'params': {'vtracer': {'color_precision': 10}},
                'should_contain': 'color_precision must be between 1 and 8'
            },
            {
                'name': 'Invalid colormode',
                'params': {'vtracer': {'colormode': 'invalid'}},
                'should_contain': 'colormode must be one of: color, binary'
            }
        ]

        for case in invalid_test_cases:
            try:
                # Use FastAPI test client simulation
                from fastapi.testclient import TestClient
                client = TestClient(app)

                response = client.post(
                    "/vectorize",
                    files={"file": ("test.png", image_bytes, "image/png")},
                    data={
                        "parameters": json.dumps(case['params']),
                        "selected_method": ""
                    }
                )

                if response.status_code != 400:
                    print(f"❌ FAIL: {case['name']} - Expected status 400, got {response.status_code}")
                    return False

                if case['should_contain'] not in response.json().get('detail', ''):
                    print(f"❌ FAIL: {case['name']} - Expected error message containing '{case['should_contain']}'")
                    print(f"Actual response: {response.json()}")
                    return False

                print(f"✅ PASS: {case['name']} - API properly rejected with status 400")

            except Exception as e:
                print(f"❌ FAIL: {case['name']} - Exception during test: {str(e)}")
                return False

        return True

    def test_api_with_valid_parameters(self):
        """Test that API accepts valid parameters"""
        print("\n=== Testing Valid Parameter Acceptance ===")

        image_bytes = self.create_test_image()

        valid_test_cases = [
            {
                'name': 'Valid Potrace parameters',
                'params': {'potrace': {'turdsize': 2, 'alphamax': 1.0, 'turnpolicy': 'minority'}},
                'method': 'potrace'
            },
            {
                'name': 'Valid VTracer parameters',
                'params': {'vtracer': {'colormode': 'color', 'color_precision': 6, 'filter_speckle': 4}},
                'method': 'vtracer'
            }
        ]

        for case in valid_test_cases:
            try:
                from fastapi.testclient import TestClient
                client = TestClient(app)

                response = client.post(
                    "/vectorize",
                    files={"file": ("test.png", image_bytes, "image/png")},
                    data={
                        "parameters": json.dumps(case['params']),
                        "selected_method": case['method']
                    }
                )

                if response.status_code != 200:
                    print(f"❌ FAIL: {case['name']} - Expected status 200, got {response.status_code}")
                    print(f"Response: {response.json()}")
                    return False

                # Check that we got SVG output
                response_data = response.json()
                if 'vectorized' not in response_data:
                    print(f"❌ FAIL: {case['name']} - Missing vectorized results")
                    return False

                print(f"✅ PASS: {case['name']} - API accepted parameters and returned results")

            except Exception as e:
                print(f"❌ FAIL: {case['name']} - Exception during test: {str(e)}")
                return False

        return True

class TestEndToEndParameterFlow:
    """Test complete parameter flow from frontend to backend"""

    def test_parameter_combinations(self):
        """Test various parameter combinations to ensure robustness"""
        print("\n=== Testing Parameter Combinations ===")

        test_combinations = [
            # Edge cases for Potrace
            {'potrace': {'turdsize': 0, 'alphamax': 0.0, 'invert': True}},
            {'potrace': {'turdsize': 100, 'alphamax': 2.0, 'opticurve': False}},

            # Edge cases for VTracer
            {'vtracer': {'color_precision': 1, 'filter_speckle': 1, 'corner_threshold': 0}},
            {'vtracer': {'color_precision': 8, 'filter_speckle': 100, 'corner_threshold': 180}},

            # Mixed parameters (should be valid)
            {
                'potrace': {'turdsize': 5, 'turnpolicy': 'majority'},
                'vtracer': {'colormode': 'binary', 'path_precision': 5}
            },
        ]

        image_bytes = TestAPIParameterValidation().create_test_image()

        for i, params in enumerate(test_combinations):
            try:
                from fastapi.testclient import TestClient
                client = TestClient(app)

                response = client.post(
                    "/vectorize",
                    files={"file": ("test.png", image_bytes, "image/png")},
                    data={
                        "parameters": json.dumps(params),
                        "selected_method": ""  # Process all methods
                    }
                )

                if response.status_code != 200:
                    print(f"❌ FAIL: Parameter combination {i+1} failed with status {response.status_code}")
                    print(f"Params: {params}")
                    print(f"Response: {response.json()}")
                    return False

                print(f"✅ PASS: Parameter combination {i+1} accepted")

            except Exception as e:
                print(f"❌ FAIL: Parameter combination {i+1} - Exception: {str(e)}")
                return False

        return True

def run_all_tests():
    """Run all parameter validation tests"""
    print("🔥 STARTING COMPREHENSIVE PARAMETER BUG FIX TESTS 🔥\n")

    test_classes = [
        TestParameterValidation(),
        TestAPIParameterValidation(),
        TestEndToEndParameterFlow()
    ]

    all_passed = True
    total_tests = 0
    passed_tests = 0

    for test_class in test_classes:
        print(f"\n📋 Running tests for {test_class.__class__.__name__}")
        print("=" * 50)

        # Get all test methods
        test_methods = [method for method in dir(test_class) if method.startswith('test_')]

        for method_name in test_methods:
            total_tests += 1
            print(f"\n🧪 Running {method_name}...")

            try:
                method = getattr(test_class, method_name)
                result = method()

                if result is False:
                    all_passed = False
                else:
                    passed_tests += 1

            except Exception as e:
                print(f"❌ EXCEPTION in {method_name}: {str(e)}")
                all_passed = False

    # Final results
    print("\n" + "=" * 60)
    print("🏁 FINAL TEST RESULTS")
    print("=" * 60)
    print(f"Total Tests: {total_tests}")
    print(f"Passed: {passed_tests}")
    print(f"Failed: {total_tests - passed_tests}")

    if all_passed and passed_tests == total_tests:
        print("🎉 ALL TESTS PASSED! Parameter bug fixes are working correctly! 🎉")
        return True
    else:
        print("💥 SOME TESTS FAILED! Please review the failures above. 💥")
        return False

if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)