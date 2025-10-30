import { describe, it, expect, beforeEach, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import App from '../App.vue'
import axios from 'axios'

// Mock axios
vi.mock('axios')
const mockedAxios = vi.mocked(axios)

describe('App.vue', () => {
  let wrapper

  beforeEach(() => {
    vi.clearAllMocks()
    wrapper = mount(App)
  })

  describe('Initial State', () => {
    it('renders correctly', () => {
      expect(wrapper.find('h1').text()).toBe('🎨 Image Vectorizer')
      expect(wrapper.find('.upload-placeholder').exists()).toBe(true)
      expect(wrapper.find('.results').exists()).toBe(false)
    })

    it('has correct initial data', () => {
      const vm = wrapper.vm
      expect(vm.isDragOver).toBe(false)
      expect(vm.originalImage).toBe(null)
      expect(vm.results).toBe(null)
      expect(vm.selectedMethod).toBe('potrace')
      expect(vm.loading).toBe(false)
      expect(vm.showParameters).toBe(false)
    })

    it('has default parameters configured', () => {
      const vm = wrapper.vm
      expect(vm.parameters.potrace.invert).toBe(false)
      expect(vm.parameters.potrace.turdsize).toBe(2)
      expect(vm.parameters.opencv_edge.blur_size).toBe(5)
      expect(vm.parameters.opencv_contour.threshold).toBe(127)
    })
  })

  describe('File Upload', () => {
    it('handles file dialog opening', async () => {
      const fileInput = wrapper.find('input[type="file"]')
      const spy = vi.spyOn(fileInput.element, 'click')

      await wrapper.find('.upload-area').trigger('click')
      expect(spy).toHaveBeenCalled()
    })

    it('handles drag events correctly', async () => {
      const uploadArea = wrapper.find('.upload-area')

      // Test dragover
      await uploadArea.trigger('dragover')
      expect(wrapper.vm.isDragOver).toBe(true)
      expect(uploadArea.classes()).toContain('dragover')

      // Test dragleave
      await uploadArea.trigger('dragleave')
      expect(wrapper.vm.isDragOver).toBe(false)
    })

    it('processes valid image files', async () => {
      // Mock successful API response
      mockedAxios.post.mockResolvedValueOnce({
        data: {
          success: true,
          original_image: 'data:image/png;base64,test',
          vectorized: {
            potrace: '<svg>test</svg>',
            opencv: '<svg>test</svg>'
          },
          parameters_used: {}
        }
      })

      const file = new File(['test'], 'test.png', { type: 'image/png' })
      await wrapper.vm.processFile(file)

      expect(wrapper.vm.originalFile).toBe(file)
      expect(wrapper.vm.error).toBe(null)
    })

    it('rejects non-image files', async () => {
      const file = new File(['test'], 'test.txt', { type: 'text/plain' })
      await wrapper.vm.processFile(file)

      expect(wrapper.vm.error).toBe('Please select a valid image file')
      expect(wrapper.vm.originalFile).toBe(null)
    })
  })

  describe('Parameter Controls', () => {
    beforeEach(async () => {
      // Set up component with results to show parameter panel
      await wrapper.setData({
        results: {
          vectorized: {
            potrace: '<svg>test</svg>',
            opencv_edge: '<svg>test</svg>',
            opencv_contour: '<svg>test</svg>',
            opencv: '<svg>test</svg>'
          }
        },
        originalFile: new File(['test'], 'test.png', { type: 'image/png' })
      })
    })

    it('toggles parameter panel visibility', async () => {
      expect(wrapper.vm.showParameters).toBe(false)
      expect(wrapper.find('.parameters-content').exists()).toBe(false)

      await wrapper.find('.toggle-params-btn').trigger('click')
      expect(wrapper.vm.showParameters).toBe(true)
      expect(wrapper.find('.parameters-content').exists()).toBe(true)
    })

    it('shows correct parameters for selected method', async () => {
      await wrapper.setData({ showParameters: true, selectedMethod: 'potrace' })
      await wrapper.vm.$nextTick()

      expect(wrapper.find('.param-section').exists()).toBe(true)
      expect(wrapper.find('h4').text()).toBe('Potrace Settings')
      expect(wrapper.find('input[type="checkbox"]').exists()).toBe(true)
    })

    it('updates potrace parameters', async () => {
      await wrapper.setData({ showParameters: true, selectedMethod: 'potrace' })
      await wrapper.vm.$nextTick()

      const invertCheckbox = wrapper.find('input[type="checkbox"]')
      await invertCheckbox.setChecked(true)

      expect(wrapper.vm.parameters.potrace.invert).toBe(true)
    })

    it('updates opencv edge parameters', async () => {
      await wrapper.setData({ showParameters: true, selectedMethod: 'opencv_edge' })
      await wrapper.vm.$nextTick()

      const blurSlider = wrapper.find('input[type="range"]')
      await blurSlider.setValue(7)

      expect(wrapper.vm.parameters.opencv_edge.blur_size).toBe('7')
    })

    it('debounces parameter changes correctly', async () => {
      vi.useFakeTimers()
      const spy = vi.spyOn(wrapper.vm, 'reprocessImage')

      await wrapper.setData({ showParameters: true, selectedMethod: 'potrace' })
      await wrapper.vm.$nextTick()

      // Call debounced function multiple times
      wrapper.vm.debouncedReprocess()
      wrapper.vm.debouncedReprocess()
      wrapper.vm.debouncedReprocess()

      // Fast-forward time
      vi.advanceTimersByTime(500)

      expect(spy).toHaveBeenCalledTimes(1)
      vi.useRealTimers()
    })
  })

  describe('Method Selection', () => {
    beforeEach(async () => {
      await wrapper.setData({
        results: {
          vectorized: {
            potrace: '<svg>potrace</svg>',
            opencv_edge: '<svg>edge</svg>',
            opencv_contour: '<svg>contour</svg>',
            opencv: '<svg>basic</svg>'
          }
        }
      })
    })

    it('displays method selector with all methods', () => {
      const select = wrapper.find('.method-selector select')
      const options = select.findAll('option')

      expect(options.length).toBe(4)
      expect(options[0].text()).toContain('Potrace')
      expect(options[1].text()).toContain('OpenCV')
    })

    it('changes selected method', async () => {
      const select = wrapper.find('.method-selector select')
      await select.setValue('opencv_edge')

      expect(wrapper.vm.selectedMethod).toBe('opencv_edge')
    })

    it('displays correct method display names', () => {
      expect(wrapper.vm.getMethodDisplayName('potrace')).toBe('Potrace (Traditional)')
      expect(wrapper.vm.getMethodDisplayName('opencv_edge')).toBe('OpenCV (Enhanced Edges)')
      expect(wrapper.vm.getMethodDisplayName('opencv_contour')).toBe('OpenCV (Filled Contours)')
      expect(wrapper.vm.getMethodDisplayName('opencv')).toBe('OpenCV (Basic)')
    })
  })

  describe('SVG Display', () => {
    it('returns empty string when no results', () => {
      expect(wrapper.vm.getCurrentSVG()).toBe('')
    })

    it('returns empty string when no selected method', async () => {
      await wrapper.setData({
        results: { vectorized: {} },
        selectedMethod: null
      })
      expect(wrapper.vm.getCurrentSVG()).toBe('')
    })

    it('returns valid SVG content', async () => {
      await wrapper.setData({
        results: {
          vectorized: {
            potrace: '<svg>test content</svg>'
          }
        },
        selectedMethod: 'potrace'
      })

      expect(wrapper.vm.getCurrentSVG()).toBe('<svg>test content</svg>')
    })

    it('handles object responses with svg property', async () => {
      await wrapper.setData({
        results: {
          vectorized: {
            potrace: { svg: '<svg>object content</svg>' }
          }
        },
        selectedMethod: 'potrace'
      })

      expect(wrapper.vm.getCurrentSVG()).toBe('<svg>object content</svg>')
    })

    it('rejects non-SVG content', async () => {
      await wrapper.setData({
        results: {
          vectorized: {
            potrace: 'not svg content'
          }
        },
        selectedMethod: 'potrace'
      })

      expect(wrapper.vm.getCurrentSVG()).toBe('')
    })
  })

  describe('Comparison Slider', () => {
    beforeEach(async () => {
      await wrapper.setData({
        results: {
          vectorized: {
            potrace: '<svg>test</svg>'
          }
        }
      })
    })

    it('has initial slider value', () => {
      expect(wrapper.vm.sliderValue).toBe(50)
    })

    it('updates clip path based on slider value', async () => {
      await wrapper.setData({ sliderValue: 75 })
      await wrapper.vm.$nextTick()

      const vectorLayer = wrapper.find('.vector-layer')
      expect(vectorLayer.attributes('style')).toContain('clipPath: inset(0 25% 0 0)')
    })

    it('positions slider line correctly', async () => {
      await wrapper.setData({ sliderValue: 30 })
      await wrapper.vm.$nextTick()

      const sliderLine = wrapper.find('.slider-line')
      expect(sliderLine.attributes('style')).toContain('left: 30%')
    })
  })

  describe('Download Functionality', () => {
    beforeEach(async () => {
      await wrapper.setData({
        results: {
          vectorized: {
            potrace: '<svg xmlns="http://www.w3.org/2000/svg">test</svg>'
          }
        },
        selectedMethod: 'potrace'
      })

      // Mock DOM methods
      const mockLink = {
        href: '',
        download: '',
        click: vi.fn(),
        remove: vi.fn()
      }
      vi.spyOn(document, 'createElement').mockReturnValue(mockLink)
      vi.spyOn(document.body, 'appendChild').mockImplementation(() => {})
      vi.spyOn(document.body, 'removeChild').mockImplementation(() => {})
    })

    it('creates download link with correct filename', () => {
      wrapper.vm.downloadSVG()

      expect(document.createElement).toHaveBeenCalledWith('a')
      expect(document.body.appendChild).toHaveBeenCalled()
    })

    it('does not download if no SVG content', async () => {
      await wrapper.setData({
        results: { vectorized: {} },
        selectedMethod: 'nonexistent'
      })

      wrapper.vm.downloadSVG()
      expect(document.createElement).not.toHaveBeenCalled()
    })
  })

  describe('API Integration', () => {
    it('calls vectorize API with correct parameters', async () => {
      mockedAxios.post.mockResolvedValueOnce({
        data: {
          success: true,
          original_image: 'data:image/png;base64,test',
          vectorized: { potrace: '<svg>test</svg>' },
          parameters_used: {}
        }
      })

      const file = new File(['test'], 'test.png', { type: 'image/png' })
      await wrapper.setData({ originalFile: file })

      await wrapper.vm.vectorizeWithCurrentParameters()

      expect(mockedAxios.post).toHaveBeenCalledWith(
        'http://localhost:8000/vectorize',
        expect.any(FormData),
        { headers: { 'Content-Type': 'multipart/form-data' } }
      )
    })

    it('handles API errors gracefully', async () => {
      mockedAxios.post.mockRejectedValueOnce({
        response: { data: { detail: 'API Error' } }
      })

      const file = new File(['test'], 'test.png', { type: 'image/png' })
      await wrapper.setData({ originalFile: file })

      await wrapper.vm.vectorizeWithCurrentParameters()

      expect(wrapper.vm.error).toBe('API Error')
    })

    it('handles network errors', async () => {
      mockedAxios.post.mockRejectedValueOnce(new Error('Network Error'))

      const file = new File(['test'], 'test.png', { type: 'image/png' })
      await wrapper.setData({ originalFile: file })

      await wrapper.vm.vectorizeWithCurrentParameters()

      expect(wrapper.vm.error).toBe('Failed to vectorize image')
    })
  })

  describe('Loading States', () => {
    it('shows loading state during processing', async () => {
      await wrapper.setData({ loading: true })
      expect(wrapper.find('.loading').exists()).toBe(true)
      expect(wrapper.find('.spinner').exists()).toBe(true)
    })

    it('shows parameter loading state', async () => {
      await wrapper.setData({
        results: { vectorized: { potrace: '<svg>test</svg>' } },
        parameterLoading: true
      })
      expect(wrapper.find('.svg-loading-overlay').exists()).toBe(true)
    })

    it('hides results during loading', async () => {
      await wrapper.setData({
        results: { vectorized: { potrace: '<svg>test</svg>' } },
        loading: true
      })
      expect(wrapper.find('.results').exists()).toBe(false)
    })
  })

  describe('Error Handling', () => {
    it('displays error message', async () => {
      await wrapper.setData({ error: 'Test error message' })

      expect(wrapper.find('.error').exists()).toBe(true)
      expect(wrapper.find('.error p').text()).toBe('Test error message')
    })

    it('clears error on new file processing', async () => {
      await wrapper.setData({ error: 'Previous error' })

      mockedAxios.post.mockResolvedValueOnce({
        data: {
          success: true,
          original_image: 'data:image/png;base64,test',
          vectorized: { potrace: '<svg>test</svg>' },
          parameters_used: {}
        }
      })

      const file = new File(['test'], 'test.png', { type: 'image/png' })
      await wrapper.vm.processFile(file)

      expect(wrapper.vm.error).toBe(null)
    })
  })

  describe('Component Cleanup', () => {
    it('cleans up timers on unmount', async () => {
      const clearTimeoutSpy = vi.spyOn(global, 'clearTimeout')
      await wrapper.setData({ parameterDebounceTimer: 123 })

      wrapper.unmount()

      expect(clearTimeoutSpy).toHaveBeenCalledWith(123)
    })
  })
})