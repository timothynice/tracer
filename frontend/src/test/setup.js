// Test setup file
import { beforeAll, afterAll, afterEach } from 'vitest'
import { setupServer } from 'msw/node'
import { http, HttpResponse } from 'msw'

// Mock API server
const handlers = [
  // Mock the vectorize endpoint
  http.post('http://localhost:8000/vectorize', ({ request }) => {
    return HttpResponse.json({
      success: true,
      original_image: 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==',
      vectorized: {
        potrace: '<?xml version="1.0" encoding="UTF-8"?><svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"><path d="M10,10 L90,10 L90,90 L10,90 Z" fill="black"/></svg>',
        opencv_edge: '<?xml version="1.0" encoding="UTF-8"?><svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"><path d="M20,20 L80,20 L80,80 L20,80 Z" fill="none" stroke="black" stroke-width="2"/></svg>',
        opencv_contour: '<?xml version="1.0" encoding="UTF-8"?><svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"><path d="M15,15 L85,15 L85,85 L15,85 Z" fill="black"/></svg>',
        opencv: '<?xml version="1.0" encoding="UTF-8"?><svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"><path d="M25,25 L75,25 L75,75 L25,75 Z" fill="black"/></svg>'
      },
      parameters_used: {
        potrace: {
          invert: false,
          turdsize: 2,
          turnpolicy: 'minority',
          alphamax: 1.0,
          opticurve: true
        },
        opencv_edge: {
          blur_size: 5,
          low_threshold: 30,
          high_threshold: 100,
          min_area: 50,
          epsilon_factor: 0.02,
          stroke_width: 2
        },
        opencv_contour: {
          threshold: 127,
          min_area: 100,
          epsilon_factor: 0.01,
          invert_threshold: false
        },
        opencv: {
          low_threshold: 50,
          high_threshold: 150,
          min_contour_points: 3
        }
      }
    })
  }),

  // Mock health endpoint
  http.get('http://localhost:8000/health', () => {
    return HttpResponse.json({
      status: 'healthy',
      message: 'Image Vectorizer API is running'
    })
  })
]

export const server = setupServer(...handlers)

// Start mock server before all tests
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }))

// Reset handlers after each test
afterEach(() => server.resetHandlers())

// Stop mock server after all tests
afterAll(() => server.close())

// Mock File API for testing file uploads
global.File = class extends Blob {
  constructor(chunks, filename, options = {}) {
    super(chunks, options)
    this.name = filename
    this.lastModified = Date.now()
  }
}

// Mock FileReader
global.FileReader = class {
  constructor() {
    this.onload = null
    this.onerror = null
    this.readyState = 0 // EMPTY
  }

  readAsDataURL(file) {
    setTimeout(() => {
      this.readyState = 2 // DONE
      if (this.onload) {
        this.onload({
          target: {
            result: 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=='
          }
        })
      }
    }, 10)
  }
}

// Mock URL methods
global.URL = {
  createObjectURL: vi.fn(() => 'blob:mock-url'),
  revokeObjectURL: vi.fn()
}

// Mock DOM methods
Object.assign(navigator, {
  clipboard: {
    writeText: vi.fn(() => Promise.resolve())
  }
})