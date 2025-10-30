#!/usr/bin/env python3

"""
Test script to reproduce the parameter change issue.
This script simulates what the frontend does when parameters are adjusted.
"""

import requests
import json
import time
import os
from PIL import Image
import io

def create_test_image():
    """Create a simple test image for debugging"""
    img = Image.new('RGB', (100, 100), color='white')
    # Add some simple content
    pixels = img.load()
    for i in range(20, 80):
        for j in range(20, 80):
            pixels[i, j] = (0, 0, 0)  # Black square

    img_bytes = io.BytesIO()
    img.save(img_bytes, format='PNG')
    return img_bytes.getvalue()

def test_initial_upload():
    """Test initial image upload with default parameters"""
    print("=== Testing Initial Upload ===")

    image_bytes = create_test_image()

    # Default parameters from frontend
    parameters = {
        'potrace': {
            'invert': False,
            'turdsize': 2,
            'turnpolicy': 'minority',
            'alphamax': 1.0,
            'opticurve': True,
        },
        'vtracer': {
            'colormode': 'color',
            'color_precision': 6,
            'filter_speckle': 4,
            'corner_threshold': 60,
            'length_threshold': 4.0,
            'max_iterations': 10,
            'splice_threshold': 45,
            'path_precision': 3
        }
    }

    files = {'file': ('test.png', image_bytes, 'image/png')}
    data = {'parameters': json.dumps(parameters)}

    try:
        response = requests.post('http://localhost:8000/vectorize', files=files, data=data)
        print(f"Status Code: {response.status_code}")
        print(f"Response: {response.json() if response.status_code == 200 else response.text}")
        return True
    except requests.exceptions.ConnectionError:
        print("ERROR: Backend server not running on http://localhost:8000")
        return False
    except Exception as e:
        print(f"ERROR: {e}")
        return False

def test_parameter_change():
    """Test parameter change with selected_method (mimics frontend behavior)"""
    print("\n=== Testing Parameter Change (VTracer) ===")

    image_bytes = create_test_image()

    # Modified parameters - change vtracer color_precision
    parameters = {
        'potrace': {
            'invert': False,
            'turdsize': 2,
            'turnpolicy': 'minority',
            'alphamax': 1.0,
            'opticurve': True,
        },
        'vtracer': {
            'colormode': 'color',
            'color_precision': 4,  # Changed from 6 to 4
            'filter_speckle': 4,
            'corner_threshold': 60,
            'length_threshold': 4.0,
            'max_iterations': 10,
            'splice_threshold': 45,
            'path_precision': 3
        }
    }

    files = {'file': ('test.png', image_bytes, 'image/png')}
    data = {
        'parameters': json.dumps(parameters),
        'selected_method': 'vtracer'  # This mimics parameter change behavior
    }

    try:
        response = requests.post('http://localhost:8000/vectorize', files=files, data=data)
        print(f"Status Code: {response.status_code}")

        if response.status_code == 200:
            result = response.json()
            print(f"Success: {result.get('success', False)}")
            print(f"Vectorized methods returned: {list(result.get('vectorized', {}).keys())}")

            # Check if VTracer result is present
            vtracer_result = result.get('vectorized', {}).get('vtracer')
            if vtracer_result:
                if vtracer_result.startswith('Error:'):
                    print(f"VTracer Error: {vtracer_result}")
                elif vtracer_result.startswith('<'):
                    print(f"VTracer SVG returned (length: {len(vtracer_result)} chars)")
                else:
                    print(f"Unexpected VTracer result format: {type(vtracer_result)}")
            else:
                print("No VTracer result in response")
        else:
            print(f"Response: {response.text}")

    except Exception as e:
        print(f"ERROR: {e}")

def test_potrace_parameter_change():
    """Test Potrace parameter change"""
    print("\n=== Testing Parameter Change (Potrace) ===")

    image_bytes = create_test_image()

    # Modified parameters - change potrace turdsize
    parameters = {
        'potrace': {
            'invert': False,
            'turdsize': 5,  # Changed from 2 to 5
            'turnpolicy': 'minority',
            'alphamax': 1.0,
            'opticurve': True,
        },
        'vtracer': {
            'colormode': 'color',
            'color_precision': 6,
            'filter_speckle': 4,
            'corner_threshold': 60,
            'length_threshold': 4.0,
            'max_iterations': 10,
            'splice_threshold': 45,
            'path_precision': 3
        }
    }

    files = {'file': ('test.png', image_bytes, 'image/png')}
    data = {
        'parameters': json.dumps(parameters),
        'selected_method': 'potrace'  # This mimics parameter change behavior
    }

    try:
        response = requests.post('http://localhost:8000/vectorize', files=files, data=data)
        print(f"Status Code: {response.status_code}")

        if response.status_code == 200:
            result = response.json()
            print(f"Success: {result.get('success', False)}")
            print(f"Vectorized methods returned: {list(result.get('vectorized', {}).keys())}")

            # Check if Potrace result is present
            potrace_result = result.get('vectorized', {}).get('potrace')
            if potrace_result:
                if potrace_result.startswith('Error:'):
                    print(f"Potrace Error: {potrace_result}")
                elif potrace_result.startswith('<'):
                    print(f"Potrace SVG returned (length: {len(potrace_result)} chars)")
                else:
                    print(f"Unexpected Potrace result format: {type(potrace_result)}")
            else:
                print("No Potrace result in response")
        else:
            print(f"Response: {response.text}")

    except Exception as e:
        print(f"ERROR: {e}")

def test_rapid_parameter_changes():
    """Test rapid parameter changes to check for race conditions"""
    print("\n=== Testing Rapid Parameter Changes ===")

    image_bytes = create_test_image()

    # Simulate rapid changes like frontend debouncing might produce
    for i in range(3):
        parameters = {
            'vtracer': {
                'colormode': 'color',
                'color_precision': 3 + i,  # Changing parameter rapidly
                'filter_speckle': 4,
                'corner_threshold': 60,
                'length_threshold': 4.0,
                'max_iterations': 10,
                'splice_threshold': 45,
                'path_precision': 3
            }
        }

        files = {'file': ('test.png', image_bytes, 'image/png')}
        data = {
            'parameters': json.dumps(parameters),
            'selected_method': 'vtracer'
        }

        print(f"Request {i+1}: color_precision = {3+i}")

        try:
            response = requests.post('http://localhost:8000/vectorize', files=files, data=data)
            print(f"  Status: {response.status_code}")

            if response.status_code == 200:
                result = response.json()
                vtracer_result = result.get('vectorized', {}).get('vtracer', '')
                if vtracer_result.startswith('Error:'):
                    print(f"  Error: {vtracer_result}")
                elif vtracer_result.startswith('<'):
                    print(f"  Success: SVG length {len(vtracer_result)}")
                else:
                    print(f"  Unexpected result: {type(vtracer_result)}")
            else:
                print(f"  Failed: {response.text}")

        except Exception as e:
            print(f"  Exception: {e}")

        # Small delay between requests
        time.sleep(0.1)

if __name__ == "__main__":
    print("Parameter API Test Script")
    print("=" * 40)

    # Test initial upload
    if not test_initial_upload():
        print("Backend not available, exiting...")
        exit(1)

    # Test parameter changes
    test_parameter_change()
    test_potrace_parameter_change()

    # Test rapid changes
    test_rapid_parameter_changes()

    print("\nTest complete. Check server logs for detailed output.")