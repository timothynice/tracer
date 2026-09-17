#!/usr/bin/env python3
"""Test runner script for the vectorizer backend."""
import sys
import subprocess
import os
from pathlib import Path


def run_command(cmd, description):
    """Run a command and handle errors."""
    print(f"\n{'='*60}")
    print(f"Running: {description}")
    print(f"Command: {' '.join(cmd)}")
    print(f"{'='*60}")

    try:
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)
        print(result.stdout)
        if result.stderr:
            print("STDERR:", result.stderr)
        return True
    except subprocess.CalledProcessError as e:
        print(f"ERROR: {description} failed with exit code {e.returncode}")
        print(f"STDOUT: {e.stdout}")
        print(f"STDERR: {e.stderr}")
        return False
    except FileNotFoundError:
        print(f"ERROR: Command not found. Make sure all dependencies are installed.")
        return False


def main():
    """Main test runner."""
    print("🧪 Vectorizer Backend Test Suite")
    print("=" * 60)

    # Change to backend directory
    backend_dir = Path(__file__).parent
    os.chdir(backend_dir)

    # Check if virtual environment is activated
    if not os.environ.get('VIRTUAL_ENV'):
        print("⚠️  Warning: Virtual environment not detected.")
        print("   Consider activating your virtual environment first:")
        print("   source venv/bin/activate  # Linux/Mac")
        print("   venv\\Scripts\\activate     # Windows")
        print()

    success_count = 0
    total_tests = 0

    # Test configurations
    test_configs = [
        {
            "cmd": ["python", "-m", "pytest", "--version"],
            "description": "Checking pytest installation",
            "required": True
        },
        {
            "cmd": ["python", "-m", "pytest", "tests/", "-v", "--tb=short"],
            "description": "Running all tests with verbose output",
            "required": True
        },
        {
            "cmd": ["python", "-m", "pytest", "tests/test_vectorizer_service.py", "-v", "-m", "unit"],
            "description": "Running unit tests for VectorizerService",
            "required": True
        },
        {
            "cmd": ["python", "-m", "pytest", "tests/test_api_endpoints.py", "-v", "-m", "integration"],
            "description": "Running API integration tests",
            "required": True
        },
        {
            "cmd": ["python", "-m", "pytest", "tests/test_parameter_system.py", "-v"],
            "description": "Running parameter system tests",
            "required": True
        },
        {
            "cmd": ["python", "-m", "pytest", "tests/test_performance.py", "-v", "-m", "performance", "--tb=short"],
            "description": "Running performance tests",
            "required": False
        },
        {
            "cmd": ["python", "-m", "pytest", "tests/", "--cov=main", "--cov-report=term-missing"],
            "description": "Running tests with coverage report",
            "required": False
        }
    ]

    for config in test_configs:
        total_tests += 1
        success = run_command(config["cmd"], config["description"])

        if success:
            success_count += 1
            print(f"✅ {config['description']} - PASSED")
        else:
            print(f"❌ {config['description']} - FAILED")
            if config["required"]:
                print("⚠️  This is a required test. Consider fixing before continuing.")

    # Summary
    print(f"\n{'='*60}")
    print(f"📊 TEST SUMMARY")
    print(f"{'='*60}")
    print(f"Total tests run: {total_tests}")
    print(f"Passed: {success_count}")
    print(f"Failed: {total_tests - success_count}")

    if success_count == total_tests:
        print("🎉 All tests passed!")
        return 0
    else:
        print("⚠️  Some tests failed. Check the output above for details.")
        return 1


if __name__ == "__main__":
    sys.exit(main())