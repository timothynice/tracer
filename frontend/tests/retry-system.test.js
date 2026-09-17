import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'
import axios from 'axios'
import App from '../src/App.vue'

// Mock axios
vi.mock('axios')
const mockedAxios = vi.mocked(axios)

describe('Retry System', () => {
  let wrapper
  let consoleSpy

  beforeEach(() => {
    // Mock console.log to capture retry logs
    consoleSpy = vi.spyOn(console, 'log').mockImplementation(() => {})

    wrapper = mount(App)

    // Set up test data
    wrapper.setData({
      originalFile: new File(['test'], 'test.png', { type: 'image/png' }),
      originalImage: 'data:image/png;base64,test'
    })

    vi.clearAllMocks()
    vi.useFakeTimers()
  })

  afterEach(() => {
    consoleSpy.restore()
    vi.restoreAllMocks()
    vi.useRealTimers()
    wrapper.unmount()
  })

  it('should succeed on first attempt without retries', async () => {
    // Mock successful response
    mockedAxios.post.mockResolvedValueOnce({
      data: { vectorized: { vtracer: '<svg>success</svg>' } }
    })

    const promise = wrapper.vm.makeRequestWithRetry('http://test.com/vectorize', new FormData())
    const response = await promise

    expect(response.data.vectorized.vtracer).toBe('<svg>success</svg>')
    expect(wrapper.vm.retryAttempt).toBe(0)
    expect(wrapper.vm.isRetrying).toBe(false)
    expect(mockedAxios.post).toHaveBeenCalledTimes(1)
  })

  it('should retry on timeout errors with proper delays', async () => {
    let attemptCount = 0

    mockedAxios.post.mockImplementation(() => {
      attemptCount++
      if (attemptCount < 3) {
        // First two attempts fail with timeout
        const error = new Error('Request timeout')
        error.code = 'ECONNABORTED'
        return Promise.reject(error)
      }
      // Third attempt succeeds
      return Promise.resolve({
        data: { vectorized: { vtracer: '<svg>success</svg>' } }
      })
    })

    const promise = wrapper.vm.makeRequestWithRetry('http://test.com/vectorize', new FormData())

    // Fast forward through first retry delay (2s)
    await vi.advanceTimersByTimeAsync(2000)

    // Fast forward through second retry delay (5s)
    await vi.advanceTimersByTimeAsync(5000)

    const response = await promise

    expect(response.data.vectorized.vtracer).toBe('<svg>success</svg>')
    expect(mockedAxios.post).toHaveBeenCalledTimes(3)
    expect(wrapper.vm.retryAttempt).toBe(0) // Should be reset after success
    expect(wrapper.vm.isRetrying).toBe(false)
  })

  it('should fail after max retries with proper error', async () => {
    // All attempts fail with timeout
    mockedAxios.post.mockRejectedValue({
      code: 'ECONNABORTED',
      message: 'Request timeout'
    })

    const promise = wrapper.vm.makeRequestWithRetry('http://test.com/vectorize', new FormData(), 2)

    // Fast forward through all retry delays
    await vi.advanceTimersByTimeAsync(10000)

    await expect(promise).rejects.toEqual({
      code: 'ECONNABORTED',
      message: 'Request timeout'
    })

    expect(mockedAxios.post).toHaveBeenCalledTimes(2)
    expect(wrapper.vm.retryAttempt).toBe(0) // Should be reset after failure
    expect(wrapper.vm.isRetrying).toBe(false)
  })

  it('should use progressive timeouts for requests', async () => {
    let callCount = 0
    const timeouts = []

    mockedAxios.post.mockImplementation((url, data, config) => {
      timeouts.push(config.timeout)
      callCount++

      if (callCount < 3) {
        const error = new Error('Request timeout')
        error.code = 'ECONNABORTED'
        return Promise.reject(error)
      }

      return Promise.resolve({
        data: { vectorized: { vtracer: '<svg>success</svg>' } }
      })
    })

    const promise = wrapper.vm.makeRequestWithRetry('http://test.com/vectorize', new FormData())

    // Advance through retry delays
    await vi.advanceTimersByTimeAsync(10000)
    await promise

    // Verify progressive timeouts: 60s, 90s, 120s
    expect(timeouts).toEqual([60000, 90000, 120000])
  })

  it('should not retry on non-retryable errors', async () => {
    // 400 Bad Request - should not retry
    mockedAxios.post.mockRejectedValueOnce({
      response: { status: 400 },
      message: 'Bad Request'
    })

    const promise = wrapper.vm.makeRequestWithRetry('http://test.com/vectorize', new FormData())

    await expect(promise).rejects.toEqual({
      response: { status: 400 },
      message: 'Bad Request'
    })

    expect(mockedAxios.post).toHaveBeenCalledTimes(1) // No retries
  })

  it('should show correct loading and retry states', async () => {
    let attemptCount = 0

    mockedAxios.post.mockImplementation(() => {
      attemptCount++
      if (attemptCount < 2) {
        const error = new Error('Request timeout')
        error.code = 'ECONNABORTED'
        return Promise.reject(error)
      }
      return Promise.resolve({
        data: { vectorized: { vtracer: '<svg>success</svg>' } }
      })
    })

    // Start the request
    const promise = wrapper.vm.makeRequestWithRetry('http://test.com/vectorize', new FormData())

    // During first attempt
    expect(wrapper.vm.retryAttempt).toBe(1)
    expect(wrapper.vm.isRetrying).toBe(false)

    // After first failure, during retry
    await vi.advanceTimersByTimeAsync(1000) // Partial delay
    expect(wrapper.vm.retryAttempt).toBe(2)
    expect(wrapper.vm.isRetrying).toBe(true)

    // Complete the retry
    await vi.advanceTimersByTimeAsync(5000)
    await promise

    // After success
    expect(wrapper.vm.retryAttempt).toBe(0)
    expect(wrapper.vm.isRetrying).toBe(false)
  })

  it('should log retry attempts with correct timing information', async () => {
    mockedAxios.post
      .mockRejectedValueOnce({ code: 'ECONNABORTED' })
      .mockResolvedValueOnce({ data: { vectorized: { vtracer: '<svg>success</svg>' } } })

    const promise = wrapper.vm.makeRequestWithRetry('http://test.com/vectorize', new FormData())
    await vi.advanceTimersByTimeAsync(3000)
    await promise

    // Check console logs for retry information
    expect(consoleSpy).toHaveBeenCalledWith('Attempt 1 failed:', 'ECONNABORTED')
    expect(consoleSpy).toHaveBeenCalledWith('Waiting 2s before retry...')
    expect(consoleSpy).toHaveBeenCalledWith('Retry attempt 2/3 with 90s timeout...')
  })
})