#!/usr/bin/env node
/**
 * Test runner script for the vectorizer frontend.
 */
import { spawn } from 'child_process';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';
import fs from 'fs';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

// ANSI color codes for console output
const colors = {
  reset: '\x1b[0m',
  bright: '\x1b[1m',
  red: '\x1b[31m',
  green: '\x1b[32m',
  yellow: '\x1b[33m',
  blue: '\x1b[34m',
  magenta: '\x1b[35m',
  cyan: '\x1b[36m'
};

function log(message, color = 'reset') {
  console.log(`${colors[color]}${message}${colors.reset}`);
}

function runCommand(command, args, description) {
  return new Promise((resolve, reject) => {
    log(`\n${'='.repeat(60)}`, 'cyan');
    log(`Running: ${description}`, 'bright');
    log(`Command: ${command} ${args.join(' ')}`, 'blue');
    log('='.repeat(60), 'cyan');

    const child = spawn(command, args, {
      stdio: 'inherit',
      shell: true,
      cwd: __dirname
    });

    child.on('close', (code) => {
      if (code === 0) {
        log(`✅ ${description} - PASSED`, 'green');
        resolve(true);
      } else {
        log(`❌ ${description} - FAILED (exit code: ${code})`, 'red');
        resolve(false);
      }
    });

    child.on('error', (error) => {
      log(`ERROR: ${error.message}`, 'red');
      reject(error);
    });
  });
}

async function checkDependencies() {
  log('🔍 Checking dependencies...', 'cyan');

  // Check if node_modules exists
  const nodeModulesPath = join(__dirname, 'node_modules');
  if (!fs.existsSync(nodeModulesPath)) {
    log('⚠️  node_modules not found. Please run "npm install" first.', 'yellow');
    return false;
  }

  // Check if package.json exists
  const packageJsonPath = join(__dirname, 'package.json');
  if (!fs.existsSync(packageJsonPath)) {
    log('❌ package.json not found.', 'red');
    return false;
  }

  return true;
}

async function main() {
  log('🧪 Vectorizer Frontend Test Suite', 'magenta');
  log('='.repeat(60), 'cyan');

  // Check dependencies
  const depsOk = await checkDependencies();
  if (!depsOk) {
    process.exit(1);
  }

  let successCount = 0;
  let totalTests = 0;

  // Test configurations
  const testConfigs = [
    {
      command: 'npx',
      args: ['vitest', '--version'],
      description: 'Checking Vitest installation',
      required: true
    },
    {
      command: 'npm',
      args: ['run', 'test:run'],
      description: 'Running all tests',
      required: true
    },
    {
      command: 'npx',
      args: ['vitest', 'run', 'src/test/App.test.js', '--reporter=verbose'],
      description: 'Running Vue component unit tests',
      required: true
    },
    {
      command: 'npx',
      args: ['vitest', 'run', 'src/test/integration.test.js', '--reporter=verbose'],
      description: 'Running integration tests',
      required: true
    },
    {
      command: 'npm',
      args: ['run', 'test:coverage'],
      description: 'Running tests with coverage report',
      required: false
    }
  ];

  // Run tests
  for (const config of testConfigs) {
    totalTests++;
    try {
      const success = await runCommand(config.command, config.args, config.description);
      if (success) {
        successCount++;
      } else if (config.required) {
        log('⚠️  This is a required test. Consider fixing before continuing.', 'yellow');
      }
    } catch (error) {
      log(`💥 Failed to run: ${config.description}`, 'red');
      log(`Error: ${error.message}`, 'red');
    }
  }

  // Summary
  log(`\n${'='.repeat(60)}`, 'cyan');
  log('📊 TEST SUMMARY', 'bright');
  log('='.repeat(60), 'cyan');
  log(`Total tests run: ${totalTests}`, 'blue');
  log(`Passed: ${successCount}`, 'green');
  log(`Failed: ${totalTests - successCount}`, 'red');

  if (successCount === totalTests) {
    log('🎉 All tests passed!', 'green');
    process.exit(0);
  } else {
    log('⚠️  Some tests failed. Check the output above for details.', 'yellow');
    process.exit(1);
  }
}

// Run the test suite
main().catch((error) => {
  log(`💥 Test runner failed: ${error.message}`, 'red');
  process.exit(1);
});