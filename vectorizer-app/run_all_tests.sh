#!/bin/bash

# Master test runner for the vectorizer application
# Runs both backend and frontend test suites

set -e  # Exit on any error

echo "🧪 Vectorizer Application - Complete Test Suite"
echo "================================================="

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Track results
BACKEND_SUCCESS=0
FRONTEND_SUCCESS=0

echo ""
echo -e "${CYAN}📋 Pre-flight Checks${NC}"
echo "================================================="

# Check if we're in the right directory
if [ ! -f "run_all_tests.sh" ]; then
    echo -e "${RED}❌ Error: Please run this script from the vectorizer-app root directory${NC}"
    exit 1
fi

# Check for backend directory
if [ ! -d "backend" ]; then
    echo -e "${RED}❌ Error: Backend directory not found${NC}"
    exit 1
fi

# Check for frontend directory
if [ ! -d "frontend" ]; then
    echo -e "${RED}❌ Error: Frontend directory not found${NC}"
    exit 1
fi

echo -e "${GREEN}✅ Directory structure looks good${NC}"

# Backend Tests
echo ""
echo -e "${BLUE}🐍 Running Backend Tests (Python/FastAPI)${NC}"
echo "================================================="

cd backend

# Check if virtual environment is recommended
if [ -z "$VIRTUAL_ENV" ] && [ -d "venv" ]; then
    echo -e "${YELLOW}⚠️  Virtual environment detected but not activated${NC}"
    echo -e "${YELLOW}   Consider running: source venv/bin/activate${NC}"
fi

# Check if dependencies are installed
if [ ! -f "requirements-test.txt" ]; then
    echo -e "${RED}❌ requirements-test.txt not found${NC}"
    exit 1
fi

echo -e "${CYAN}📦 Installing/checking backend test dependencies...${NC}"
pip install -r requirements-test.txt > /dev/null 2>&1 || {
    echo -e "${YELLOW}⚠️  Could not install test dependencies. Continuing anyway...${NC}"
}

# Run backend tests
echo -e "${CYAN}🏃 Running backend tests...${NC}"
if python run_tests.py; then
    BACKEND_SUCCESS=1
    echo -e "${GREEN}✅ Backend tests completed successfully${NC}"
else
    echo -e "${RED}❌ Backend tests failed${NC}"
fi

# Return to root directory
cd ..

# Frontend Tests
echo ""
echo -e "${BLUE}🌐 Running Frontend Tests (Vue.js/Vitest)${NC}"
echo "================================================="

cd frontend

# Check if node_modules exists
if [ ! -d "node_modules" ]; then
    echo -e "${CYAN}📦 Installing frontend dependencies...${NC}"
    npm install || {
        echo -e "${RED}❌ Failed to install frontend dependencies${NC}"
        exit 1
    }
fi

# Run frontend tests
echo -e "${CYAN}🏃 Running frontend tests...${NC}"
if node run_tests.js; then
    FRONTEND_SUCCESS=1
    echo -e "${GREEN}✅ Frontend tests completed successfully${NC}"
else
    echo -e "${RED}❌ Frontend tests failed${NC}"
fi

# Return to root directory
cd ..

# Final Summary
echo ""
echo "================================================="
echo -e "${CYAN}📊 Final Test Summary${NC}"
echo "================================================="

if [ $BACKEND_SUCCESS -eq 1 ]; then
    echo -e "${GREEN}✅ Backend Tests: PASSED${NC}"
else
    echo -e "${RED}❌ Backend Tests: FAILED${NC}"
fi

if [ $FRONTEND_SUCCESS -eq 1 ]; then
    echo -e "${GREEN}✅ Frontend Tests: PASSED${NC}"
else
    echo -e "${RED}❌ Frontend Tests: FAILED${NC}"
fi

echo ""

# Overall result
if [ $BACKEND_SUCCESS -eq 1 ] && [ $FRONTEND_SUCCESS -eq 1 ]; then
    echo -e "${GREEN}🎉 All tests passed! Your vectorizer application is working correctly.${NC}"
    echo ""
    echo -e "${CYAN}Next steps:${NC}"
    echo "• Deploy your application with confidence"
    echo "• Monitor performance in production"
    echo "• Add new tests when adding features"
    exit 0
elif [ $BACKEND_SUCCESS -eq 1 ] || [ $FRONTEND_SUCCESS -eq 1 ]; then
    echo -e "${YELLOW}⚠️  Partial success: Some tests passed, some failed.${NC}"
    echo ""
    echo -e "${CYAN}Recommendations:${NC}"
    if [ $BACKEND_SUCCESS -eq 0 ]; then
        echo "• Fix backend issues before deploying"
        echo "• Check parameter handling and API endpoints"
    fi
    if [ $FRONTEND_SUCCESS -eq 0 ]; then
        echo "• Fix frontend issues before deploying"
        echo "• Check Vue component functionality and integrations"
    fi
    exit 1
else
    echo -e "${RED}💥 All tests failed. Please review the output above.${NC}"
    echo ""
    echo -e "${CYAN}Debugging tips:${NC}"
    echo "• Check all dependencies are installed"
    echo "• Verify Python and Node.js versions"
    echo "• Review error messages in the test output"
    echo "• Consider running backend and frontend tests separately"
    exit 1
fi