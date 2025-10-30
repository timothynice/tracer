#!/usr/bin/env python3
"""
API Parameter Test - Tests the actual /vectorize endpoint
Tests that parameters are correctly processed through the API endpoint
"""

import asyncio
import io
import json
import httpx
import hashlib
from PIL import Image, ImageDraw
import os
import sys

async def test_api_parameters():
    """Test the API endpoint with various parameters"""

    # Create test image
    img = Image.new('RGB', (150, 150), 'white')
    draw = ImageDraw.Draw(img)

    # Draw content that will show parameter effects
    draw.rectangle([30, 30, 120, 120], fill='black')
    draw.ellipse([50, 50, 100, 100], fill='white')

    # Add some noise for turdsize testing
    import random
    for _ in range(50):
        x, y = random.randint(0, 150), random.randint(0, 150)
        draw.rectangle([x, y, x+2, y+2], fill='black')

    # Convert to bytes
    img_bytes = io.BytesIO()
    img.save(img_bytes, format='PNG')
    img_bytes.seek(0)

    print("API Parameter Validation Test")
    print("=" * 50)

    # Test different parameter combinations
    test_cases = [
        {
            "name": "Potrace with different turdsize",
            "selected_method": "potrace",
            "parameters": {
                "potrace": {
                    "turdsize": 0
                }
            }
        },
        {
            "name": "Potrace with high turdsize",
            "selected_method": "potrace",
            "parameters": {
                "potrace": {
                    "turdsize": 20
                }
            }
        },
        {
            "name": "Potrace with invert",
            "selected_method": "potrace",
            "parameters": {
                "potrace": {
                    "invert": True,
                    "turdsize": 5
                }
            }
        },
        {
            "name": "OpenCV Edge with low thresholds",
            "selected_method": "opencv_edge",
            "parameters": {
                "opencv_edge": {
                    "low_threshold": 20,
                    "high_threshold": 60,
                    "min_area": 30
                }
            }
        },
        {
            "name": "OpenCV Edge with high thresholds",
            "selected_method": "opencv_edge",
            "parameters": {
                "opencv_edge": {
                    "low_threshold": 100,
                    "high_threshold": 200,
                    "min_area": 100
                }
            }
        },
        {
            "name": "OpenCV Contour with low threshold",
            "selected_method": "opencv_contour",
            "parameters": {
                "opencv_contour": {
                    "threshold": 80,
                    "min_area": 50
                }
            }
        },
        {
            "name": "OpenCV Contour with high threshold",
            "selected_method": "opencv_contour",
            "parameters": {
                "opencv_contour": {
                    "threshold": 180,
                    "min_area": 50
                }
            }
        }
    ]

    results = []

    async with httpx.AsyncClient(timeout=30.0) as client:
        for i, test_case in enumerate(test_cases):
            print(f"\n{i+1}. {test_case['name']}:")

            try:
                # Prepare the request
                img_bytes.seek(0)
                files = {"file": ("test.png", img_bytes, "image/png")}
                data = {
                    "parameters": json.dumps(test_case["parameters"]),
                    "selected_method": test_case["selected_method"]
                }

                # Make API request
                response = await client.post(
                    "http://localhost:8000/vectorize",
                    files=files,
                    data=data
                )

                if response.status_code == 200:
                    result = response.json()

                    if result["success"]:
                        method_result = result["vectorized"].get(test_case["selected_method"])

                        if method_result and not method_result.startswith("Error:"):
                            svg_hash = hashlib.md5(method_result.encode()).hexdigest()[:8]
                            path_count = method_result.count('<path')
                            svg_length = len(method_result)

                            print(f"   ✅ SUCCESS: Hash={svg_hash}, Paths={path_count}, Length={svg_length}")

                            # Check if parameters were processed
                            params_used = result.get("parameters_used", {})
                            method_params = params_used.get(test_case["selected_method"], {})

                            if method_params:
                                print(f"   📋 Parameters used: {method_params}")
                            else:
                                print(f"   ⚠️  No parameters recorded in response")

                            results.append({
                                "test": test_case["name"],
                                "hash": svg_hash,
                                "paths": path_count,
                                "length": svg_length,
                                "params": method_params,
                                "success": True
                            })
                        else:
                            print(f"   ❌ METHOD ERROR: {method_result}")
                            results.append({
                                "test": test_case["name"],
                                "error": method_result,
                                "success": False
                            })
                    else:
                        print(f"   ❌ API ERROR: Response indicates failure")
                        results.append({
                            "test": test_case["name"],
                            "error": "API response success=false",
                            "success": False
                        })
                else:
                    print(f"   ❌ HTTP ERROR: {response.status_code}")
                    print(f"      Response: {response.text[:200]}")
                    results.append({
                        "test": test_case["name"],
                        "error": f"HTTP {response.status_code}",
                        "success": False
                    })

            except Exception as e:
                print(f"   ❌ EXCEPTION: {str(e)}")
                results.append({
                    "test": test_case["name"],
                    "error": str(e),
                    "success": False
                })

    print("\n" + "=" * 50)
    print("API PARAMETER TEST SUMMARY")
    print("=" * 50)

    successful_tests = [r for r in results if r["success"]]
    failed_tests = [r for r in results if not r["success"]]

    print(f"Total tests: {len(results)}")
    print(f"Successful: {len(successful_tests)}")
    print(f"Failed: {len(failed_tests)}")

    if successful_tests:
        print(f"\nSuccessful results:")

        # Group by method to compare parameter effects
        potrace_results = [r for r in successful_tests if "potrace" in r["test"].lower()]
        opencv_edge_results = [r for r in successful_tests if "opencv edge" in r["test"].lower()]
        opencv_contour_results = [r for r in successful_tests if "opencv contour" in r["test"].lower()]

        def analyze_method_results(method_name, method_results):
            if len(method_results) >= 2:
                hashes = [r["hash"] for r in method_results]
                unique_hashes = len(set(hashes))
                print(f"\n{method_name}:")
                for result in method_results:
                    print(f"  - {result['test']}: {result['hash']} ({result['paths']} paths)")
                print(f"  → {unique_hashes}/{len(method_results)} unique results")

                if unique_hashes == len(method_results):
                    print(f"  ✅ All parameters produce different outputs")
                elif unique_hashes > 1:
                    print(f"  ⚖️  Some parameters produce different outputs")
                else:
                    print(f"  ❌ All parameters produce identical outputs")

        analyze_method_results("POTRACE", potrace_results)
        analyze_method_results("OPENCV EDGE", opencv_edge_results)
        analyze_method_results("OPENCV CONTOUR", opencv_contour_results)

    if failed_tests:
        print(f"\nFailed tests:")
        for result in failed_tests:
            print(f"  - {result['test']}: {result['error']}")

    print(f"\n" + "=" * 50)
    if len(successful_tests) == len(results):
        print("🎉 ALL TESTS PASSED - API parameter processing is working correctly!")
    elif len(successful_tests) > len(failed_tests):
        print("⚖️  MOSTLY WORKING - Some parameters working, some issues detected")
    else:
        print("❌ MAJOR ISSUES - API parameter processing needs attention")

    return results

if __name__ == "__main__":
    print("Starting API parameter validation...")
    print("Make sure the backend server is running at http://localhost:8000")
    print("Starting test in 3 seconds...")

    import time
    time.sleep(3)

    asyncio.run(test_api_parameters())