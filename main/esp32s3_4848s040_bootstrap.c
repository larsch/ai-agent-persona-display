#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#include <stdlib.h>
#include <stdio.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "driver/uart.h"
#include "driver/ledc.h"
#include "driver/gpio.h"

#include "esp_attr.h"
#include "esp_check.h"
#include "esp_heap_caps.h"
#include "esp_lcd_panel_io.h"
#include "esp_lcd_panel_io_additions.h"
#include "esp_lcd_panel_ops.h"
#include "esp_lcd_panel_rgb.h"
#include "esp_lcd_panel_vendor.h"
#include "esp_lcd_st7701.h"
#include "esp_log.h"
#include "esp_rom_sys.h"
#include "esp_timer.h"

#include "JPEGDEC.h"

/* ────────── Display ────────── */
#define LCD_H_RES 480
#define LCD_V_RES 480
#define LCD_BITS_PER_PIXEL 16
#define LCD_BYTES_PER_PIXEL 2
#define LCD_FB_SIZE (LCD_H_RES * LCD_V_RES * LCD_BYTES_PER_PIXEL)

#define LCD_PIN_PCLK    21
#define LCD_PIN_DE      18
#define LCD_PIN_VSYNC   17
#define LCD_PIN_HSYNC   16
#define LCD_PIN_SPI_CS  39
#define LCD_PIN_SPI_SCL 48
#define LCD_PIN_SPI_SDA 47
#define LCD_PIN_BL      38
#define LCD_PCLK_HZ (10 * 1000 * 1000)

#define RGB_DATA_PIN_BLUE_0   4
#define RGB_DATA_PIN_BLUE_1   5
#define RGB_DATA_PIN_BLUE_2   6
#define RGB_DATA_PIN_BLUE_3   7
#define RGB_DATA_PIN_BLUE_4   15
#define RGB_DATA_PIN_GREEN_0  8
#define RGB_DATA_PIN_GREEN_1  20
#define RGB_DATA_PIN_GREEN_2  3
#define RGB_DATA_PIN_GREEN_3  46
#define RGB_DATA_PIN_GREEN_4  9
#define RGB_DATA_PIN_GREEN_5  10
#define RGB_DATA_PIN_RED_0    11
#define RGB_DATA_PIN_RED_1    12
#define RGB_DATA_PIN_RED_2    13
#define RGB_DATA_PIN_RED_3    14
#define RGB_DATA_PIN_RED_4    0

/* ────────── UART ────────── */
#define IMAGE_UART_PORT      UART_NUM_0
#define IMAGE_UART_BAUD      3000000
#define IMAGE_UART_RX_BUF    4096
#define IMAGE_UART_TX_BUF    256
#define IMAGE_MAGIC          0x21474D49  /* "IMG!" */
#define IMAGE_MAX_JPEG_SIZE  (512 * 1024)

/* ────────── ST7701 init ────────── */
static const st7701_lcd_init_cmd_t s_st7701_type9_init_ops[] = {
    {0xFF, (uint8_t[]){0x77, 0x01, 0x00, 0x00, 0x10}, 5, 0},
    {0xC0, (uint8_t[]){0x3B, 0x00}, 2, 0},
    {0xC1, (uint8_t[]){0x0D, 0x02}, 2, 0},
    {0xC2, (uint8_t[]){0x31, 0x05}, 2, 0},
    {0xCD, (uint8_t[]){0x00}, 1, 0},
    {0xB0, (uint8_t[]){0x00, 0x11, 0x18, 0x0E, 0x11, 0x06, 0x07, 0x08,
                      0x07, 0x22, 0x04, 0x12, 0x0F, 0xAA, 0x31, 0x18}, 16, 0},
    {0xB1, (uint8_t[]){0x00, 0x11, 0x19, 0x0E, 0x12, 0x07, 0x08, 0x08,
                      0x08, 0x22, 0x04, 0x11, 0x11, 0xA9, 0x32, 0x18}, 16, 0},
    {0xFF, (uint8_t[]){0x77, 0x01, 0x00, 0x00, 0x11}, 5, 0},
    {0xB0, (uint8_t[]){0x60}, 1, 0},
    {0xB1, (uint8_t[]){0x32}, 1, 0},
    {0xB2, (uint8_t[]){0x07}, 1, 0},
    {0xB3, (uint8_t[]){0x80}, 1, 0},
    {0xB5, (uint8_t[]){0x49}, 1, 0},
    {0xB7, (uint8_t[]){0x85}, 1, 0},
    {0xB8, (uint8_t[]){0x21}, 1, 0},
    {0xC1, (uint8_t[]){0x78}, 1, 0},
    {0xC2, (uint8_t[]){0x78}, 1, 0},
    {0xE0, (uint8_t[]){0x00, 0x1B, 0x02}, 3, 0},
    {0xE1, (uint8_t[]){0x08, 0xA0, 0x00, 0x00, 0x07, 0xA0, 0x00, 0x00,
                      0x00, 0x44, 0x44}, 11, 0},
    {0xE2, (uint8_t[]){0x11, 0x11, 0x44, 0x44, 0xED, 0xA0, 0x00, 0x00,
                      0xEC, 0xA0, 0x00, 0x00}, 12, 0},
    {0xE3, (uint8_t[]){0x00, 0x00, 0x11, 0x11}, 4, 0},
    {0xE4, (uint8_t[]){0x44, 0x44}, 2, 0},
    {0xE5, (uint8_t[]){0x0A, 0xE9, 0xD8, 0xA0, 0x0C, 0xEB, 0xD8, 0xA0,
                      0x0E, 0xED, 0xD8, 0xA0, 0x10, 0xEF, 0xD8, 0xA0}, 16, 0},
    {0xE6, (uint8_t[]){0x00, 0x00, 0x11, 0x11}, 4, 0},
    {0xE7, (uint8_t[]){0x44, 0x44}, 2, 0},
    {0xE8, (uint8_t[]){0x09, 0xE8, 0xD8, 0xA0, 0x0B, 0xEA, 0xD8, 0xA0,
                      0x0D, 0xEC, 0xD8, 0xA0, 0x0F, 0xEE, 0xD8, 0xA0}, 16, 0},
    {0xEB, (uint8_t[]){0x02, 0x00, 0xE4, 0xE4, 0x88, 0x00, 0x40}, 7, 0},
    {0xEC, (uint8_t[]){0x3C, 0x00}, 2, 0},
    {0xED, (uint8_t[]){0xAB, 0x89, 0x76, 0x54, 0x02, 0xFF, 0xFF, 0xFF,
                      0xFF, 0xFF, 0xFF, 0x20, 0x45, 0x67, 0x98, 0xBA}, 16, 0},
    {0xFF, (uint8_t[]){0x77, 0x01, 0x00, 0x00, 0x13}, 5, 0},
    {0xE5, (uint8_t[]){0xE4}, 1, 0},
    {0xFF, (uint8_t[]){0x77, 0x01, 0x00, 0x00, 0x00}, 5, 0},
    {0x3A, (uint8_t[]){0x60}, 1, 0},
    {0x11, NULL, 0, 120},
    {0x29, NULL, 0, 0},
};

static const char *TAG = "image_disp";

static esp_lcd_panel_handle_t s_panel;
static uint16_t *s_image_fb;   /* decode buffer — driver copies from this into its VSYNC-swapped double FB */
static JPEGIMAGE s_jpeg_img; /* ~17KB — must NOT be on the stack */

/* ────────── Helpers ────────── */
static inline uint16_t rgb565(uint8_t r, uint8_t g, uint8_t b)
{
    return (uint16_t)(((r & 0xF8U) << 8) | ((g & 0xFCU) << 3) | (b >> 3));
}

/* ────────── Backlight ────────── */
static void configure_backlight(void)
{
    const ledc_timer_config_t timer_cfg = {
        .speed_mode = LEDC_LOW_SPEED_MODE,
        .duty_resolution = LEDC_TIMER_10_BIT,
        .timer_num = LEDC_TIMER_0,
        .freq_hz = 150,
        .clk_cfg = LEDC_AUTO_CLK,
    };
    ESP_ERROR_CHECK(ledc_timer_config(&timer_cfg));
    const ledc_channel_config_t channel_cfg = {
        .gpio_num = LCD_PIN_BL,
        .speed_mode = LEDC_LOW_SPEED_MODE,
        .channel = LEDC_CHANNEL_0,
        .intr_type = LEDC_INTR_DISABLE,
        .timer_sel = LEDC_TIMER_0,
        .duty = 1023,
        .hpoint = 0,
    };
    ESP_ERROR_CHECK(ledc_channel_config(&channel_cfg));
}

/* ────────── Panel timing ────────── */
static esp_lcd_rgb_timing_t make_panel_timing(void)
{
    esp_lcd_rgb_timing_t timing = {
        .pclk_hz = LCD_PCLK_HZ,
        .h_res = LCD_H_RES,
        .v_res = LCD_V_RES,
        .hsync_pulse_width = 8,
        .hsync_back_porch = 50,
        .hsync_front_porch = 10,
        .vsync_pulse_width = 8,
        .vsync_back_porch = 20,
        .vsync_front_porch = 10,
        .flags = {
            .hsync_idle_low = false,
            .vsync_idle_low = false,
            .de_idle_high = false,
            .pclk_active_neg = false,
            .pclk_idle_high = false,
        },
    };
    return timing;
}

/* ────────── ST7701 SPI I/O ────────── */
static esp_lcd_panel_io_handle_t new_st7701_io(void)
{
    spi_line_config_t line_config = {
        .cs_io_type = IO_TYPE_GPIO,
        .cs_gpio_num = LCD_PIN_SPI_CS,
        .scl_io_type = IO_TYPE_GPIO,
        .scl_gpio_num = LCD_PIN_SPI_SCL,
        .sda_io_type = IO_TYPE_GPIO,
        .sda_gpio_num = LCD_PIN_SPI_SDA,
        .io_expander = NULL,
    };
    esp_lcd_panel_io_3wire_spi_config_t io_config =
        ST7701_PANEL_IO_3WIRE_SPI_CONFIG(line_config, 0);
    esp_lcd_panel_io_handle_t io_handle = NULL;
    ESP_ERROR_CHECK(esp_lcd_new_panel_io_3wire_spi(&io_config, &io_handle));
    return io_handle;
}

/* ────────── RGB panel config ────────── */
static esp_lcd_rgb_panel_config_t make_rgb_config(bool no_fb)
{
    esp_lcd_rgb_panel_config_t rgb_config = {
        .clk_src = LCD_CLK_SRC_DEFAULT,
        .timings = make_panel_timing(),
        .data_width = 16,
        .in_color_format = LCD_COLOR_FMT_RGB565,
        .out_color_format = LCD_COLOR_FMT_RGB565,
        .num_fbs = 2,
        .bounce_buffer_size_px = no_fb ? (LCD_H_RES * 10) : 0,
        .dma_burst_size = 64,
        .hsync_gpio_num = LCD_PIN_HSYNC,
        .vsync_gpio_num = LCD_PIN_VSYNC,
        .de_gpio_num = LCD_PIN_DE,
        .pclk_gpio_num = LCD_PIN_PCLK,
        .disp_gpio_num = -1,
        .data_gpio_nums = {
            RGB_DATA_PIN_BLUE_0,  RGB_DATA_PIN_BLUE_1,
            RGB_DATA_PIN_BLUE_2,  RGB_DATA_PIN_BLUE_3,
            RGB_DATA_PIN_BLUE_4,
            RGB_DATA_PIN_GREEN_0, RGB_DATA_PIN_GREEN_1,
            RGB_DATA_PIN_GREEN_2, RGB_DATA_PIN_GREEN_3,
            RGB_DATA_PIN_GREEN_4, RGB_DATA_PIN_GREEN_5,
            RGB_DATA_PIN_RED_0,   RGB_DATA_PIN_RED_1,
            RGB_DATA_PIN_RED_2,   RGB_DATA_PIN_RED_3,
            RGB_DATA_PIN_RED_4,
        },
        .flags = {
            .fb_in_psram = no_fb ? 0 : 1,
            .no_fb = no_fb ? 1 : 0,
        },
    };
    return rgb_config;
}

/* ────────── Panel init ────────── */
static esp_lcd_panel_handle_t init_panel_with_fb(void)
{
    esp_lcd_panel_io_handle_t io_handle = new_st7701_io();
    esp_lcd_rgb_panel_config_t rgb_config = make_rgb_config(false);
    st7701_vendor_config_t vendor_config = {
        .rgb_config = &rgb_config,
        .init_cmds = s_st7701_type9_init_ops,
        .init_cmds_size = sizeof(s_st7701_type9_init_ops) /
                          sizeof(s_st7701_type9_init_ops[0]),
        .flags = { .mirror_by_cmd = 1 },
    };
    const esp_lcd_panel_dev_config_t panel_config = {
        .reset_gpio_num = -1,
        .rgb_ele_order = LCD_RGB_ELEMENT_ORDER_RGB,
        .bits_per_pixel = LCD_BITS_PER_PIXEL,
        .vendor_config = &vendor_config,
    };
    esp_lcd_panel_handle_t panel = NULL;
    ESP_ERROR_CHECK(esp_lcd_new_panel_st7701(io_handle, &panel_config, &panel));
    ESP_ERROR_CHECK(esp_lcd_panel_reset(panel));
    ESP_ERROR_CHECK(esp_lcd_panel_init(panel));
    ESP_ERROR_CHECK(esp_lcd_panel_disp_on_off(panel, true));
    return panel;
}

/* ────────── Waiting screen ────────── */
static void draw_waiting_screen(esp_lcd_panel_handle_t panel)
{
    uint16_t *fb = heap_caps_malloc(LCD_FB_SIZE, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    ESP_ERROR_CHECK(fb ? ESP_OK : ESP_ERR_NO_MEM);
    for (int y = 0; y < LCD_V_RES; y++) {
        for (int x = 0; x < LCD_H_RES; x++) {
            uint8_t r = (uint8_t)((x * 255) / LCD_H_RES);
            uint8_t g = (uint8_t)((y * 255) / LCD_V_RES);
            fb[y * LCD_H_RES + x] = rgb565(r, g, 64);
        }
    }
    for (int x = 0; x < LCD_H_RES; x++) {
        fb[0 * LCD_H_RES + x] = 0xFFFF;
        fb[(LCD_V_RES - 1) * LCD_H_RES + x] = 0xFFFF;
    }
    for (int y = 0; y < LCD_V_RES; y++) {
        fb[y * LCD_H_RES + 0] = 0xFFFF;
        fb[y * LCD_H_RES + (LCD_H_RES - 1)] = 0xFFFF;
    }
    int bx = 140, by = 210, bw = 200, bh = 60;
    for (int y = by; y < by + bh; y++) {
        for (int x = bx; x < bx + bw; x++) {
            fb[y * LCD_H_RES + x] = (x == bx || x == bx + bw - 1 ||
                                     y == by || y == by + bh - 1) ? 0xFFFF : 0x0000;
        }
    }
    ESP_ERROR_CHECK(esp_lcd_panel_draw_bitmap(panel, 0, 0, LCD_H_RES, LCD_V_RES, fb));
    free(fb);
}

/* ────────── Display image ────────── */
static void display_image(esp_lcd_panel_handle_t panel, const uint16_t *pixels,
                          int img_w, int img_h)
{
    if (img_w == LCD_H_RES && img_h == LCD_V_RES) {
        ESP_ERROR_CHECK(esp_lcd_panel_draw_bitmap(panel, 0, 0,
                          LCD_H_RES, LCD_V_RES, pixels));
        return;
    }
    uint16_t *fb = heap_caps_malloc(LCD_FB_SIZE, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!fb) return;
    memset(fb, 0, LCD_FB_SIZE);
    int off_x = (LCD_H_RES - img_w) / 2;
    int off_y = (LCD_V_RES - img_h) / 2;
    if (off_x < 0) off_x = 0;
    if (off_y < 0) off_y = 0;
    int copy_w = (img_w > LCD_H_RES) ? LCD_H_RES : img_w;
    int copy_h = (img_h > LCD_V_RES) ? LCD_V_RES : img_h;
    for (int y = 0; y < copy_h; y++) {
        memcpy(fb + ((off_y + y) * LCD_H_RES) + off_x,
               pixels + (y * img_w), copy_w * sizeof(uint16_t));
    }
    ESP_ERROR_CHECK(esp_lcd_panel_draw_bitmap(panel, 0, 0, LCD_H_RES, LCD_V_RES, fb));
    free(fb);
}

/* ────────── UART helpers ────────── */
static esp_err_t uart_read_exact(uint8_t *buf, size_t len, TickType_t timeout)
{
    size_t total = 0;
    TickType_t start = xTaskGetTickCount();
    while (total < len) {
        TickType_t elapsed = xTaskGetTickCount() - start;
        TickType_t remaining = (timeout == portMAX_DELAY) ? portMAX_DELAY
                               : (elapsed >= timeout) ? 0 : (timeout - elapsed);
        if (remaining == 0 && timeout != portMAX_DELAY) return ESP_ERR_TIMEOUT;
        int rx = uart_read_bytes(IMAGE_UART_PORT, buf + total, len - total, remaining);
        if (rx < 0) return ESP_FAIL;
        total += rx;
    }
    return ESP_OK;
}

/* ────────── JPEGDEC draw callback ────────── */
static int jpeg_draw_callback(JPEGDRAW *pDraw)
{
    uint16_t *dest = s_image_fb + (pDraw->y * LCD_H_RES) + pDraw->x;
    for (int row = 0; row < pDraw->iHeight; row++) {
        memcpy(dest + row * LCD_H_RES,
               pDraw->pPixels + row * pDraw->iWidth,
               pDraw->iWidth * sizeof(uint16_t));
    }
    return 1; /* continue decoding */
}

/* ────────── Decode + display ────────── */
static esp_err_t decode_and_display(const uint8_t *jpeg_data, size_t jpeg_size)
{
    int64_t t_start = esp_timer_get_time();
    if (!s_image_fb) return ESP_ERR_NO_MEM;

    if (!JPEG_openRAM(&s_jpeg_img, (uint8_t *)jpeg_data, (int)jpeg_size,
                      jpeg_draw_callback)) {
        return ESP_FAIL;
    }
    int width = JPEG_getWidth(&s_jpeg_img);
    int height = JPEG_getHeight(&s_jpeg_img);

    if (!JPEG_decode(&s_jpeg_img, 0, 0, 0)) {
        JPEG_close(&s_jpeg_img);
        return ESP_FAIL;
    }
    JPEG_close(&s_jpeg_img);

    int64_t t_decoded = esp_timer_get_time();

    display_image(s_panel, s_image_fb, width, height);

    int64_t t_displayed = esp_timer_get_time();

    char ack[64];
    int ack_len = snprintf(ack, sizeof(ack), "OK %lld %lld\n",
                           (long long)(t_decoded - t_start),
                           (long long)(t_displayed - t_decoded));
    uart_write_bytes(IMAGE_UART_PORT, ack, ack_len);
    return ESP_OK;
}

/* ────────── Receive one image ────────── */
static esp_err_t receive_and_display_image(void)
{
    uint8_t header[8];
    esp_err_t ret = uart_read_exact(header, sizeof(header), portMAX_DELAY);
    if (ret != ESP_OK) return ret;
    uint32_t magic = (uint32_t)header[0] | ((uint32_t)header[1] << 8) |
                     ((uint32_t)header[2] << 16) | ((uint32_t)header[3] << 24);
    uint32_t jpeg_size = (uint32_t)header[4] | ((uint32_t)header[5] << 8) |
                         ((uint32_t)header[6] << 16) | ((uint32_t)header[7] << 24);
    if (magic != IMAGE_MAGIC) { ESP_LOGW(TAG, "Bad magic"); return ESP_ERR_INVALID_ARG; }
    if (jpeg_size == 0 || jpeg_size > IMAGE_MAX_JPEG_SIZE) {
        ESP_LOGW(TAG, "Bad size %lu", jpeg_size); return ESP_ERR_INVALID_SIZE;
    }
    uint8_t *jpeg_buf = heap_caps_malloc(jpeg_size, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!jpeg_buf) return ESP_ERR_NO_MEM;
    ret = uart_read_exact(jpeg_buf, jpeg_size, pdMS_TO_TICKS(30000));
    if (ret != ESP_OK) { free(jpeg_buf); return ret; }
    ret = decode_and_display(jpeg_buf, jpeg_size);
    free(jpeg_buf);
    return ret;
}

/* ────────── Re-sync helper ────────── */
static void resync_stream(void)
{
    ESP_LOGW(TAG, "Re-syncing...");
    uint8_t byte;
    while (1) {
        if (uart_read_bytes(IMAGE_UART_PORT, &byte, 1, pdMS_TO_TICKS(200)) == 1 && byte == 'I') {
            uint8_t peek[3];
            if (uart_read_bytes(IMAGE_UART_PORT, peek, 3, pdMS_TO_TICKS(500)) == 3 &&
                peek[0] == 'M' && peek[1] == 'G' && peek[2] == '!') {
                uint8_t size_buf[4];
                if (uart_read_exact(size_buf, 4, pdMS_TO_TICKS(2000)) == ESP_OK) {
                    uint32_t jpeg_size = (uint32_t)size_buf[0] |
                        ((uint32_t)size_buf[1] << 8) | ((uint32_t)size_buf[2] << 16) |
                        ((uint32_t)size_buf[3] << 24);
                    if (jpeg_size > 0 && jpeg_size <= IMAGE_MAX_JPEG_SIZE) {
                        uint8_t *jpeg_buf = heap_caps_malloc(jpeg_size, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
                        if (jpeg_buf) {
                            if (uart_read_exact(jpeg_buf, jpeg_size, pdMS_TO_TICKS(30000)) == ESP_OK)
                                decode_and_display(jpeg_buf, jpeg_size);
                            free(jpeg_buf);
                        }
                    }
                }
                return;
            }
        }
    }
}

/* ────────── Receiver task ────────── */
static void image_receiver_task(void *arg)
{
    (void)arg;
    while (1) {
        esp_err_t ret = receive_and_display_image();
        if (ret == ESP_ERR_INVALID_ARG || ret == ESP_ERR_INVALID_SIZE)
            resync_stream();
        else if (ret != ESP_OK)
            vTaskDelay(pdMS_TO_TICKS(500));
    }
}

/* ────────── Main ────────── */
void app_main(void)
{
    ESP_LOGI(TAG, "=== Image Display Firmware ===");
    configure_backlight();
    ESP_LOGI(TAG, "Init ST7701...");
    s_panel = init_panel_with_fb();
    s_image_fb = heap_caps_malloc(LCD_FB_SIZE, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    ESP_ERROR_CHECK(s_image_fb ? ESP_OK : ESP_ERR_NO_MEM);
    draw_waiting_screen(s_panel);

    const uart_config_t uart_cfg = {
        .baud_rate = IMAGE_UART_BAUD,
        .data_bits = UART_DATA_8_BITS,
        .parity = UART_PARITY_DISABLE,
        .stop_bits = UART_STOP_BITS_1,
        .flow_ctrl = UART_HW_FLOWCTRL_DISABLE,
        .source_clk = UART_SCLK_DEFAULT,
    };
    ESP_ERROR_CHECK(uart_driver_install(IMAGE_UART_PORT, IMAGE_UART_RX_BUF,
                                        IMAGE_UART_TX_BUF, 0, NULL, 0));
    ESP_ERROR_CHECK(uart_param_config(IMAGE_UART_PORT, &uart_cfg));
    ESP_ERROR_CHECK(uart_set_pin(IMAGE_UART_PORT, UART_PIN_NO_CHANGE,
                                 UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE,
                                 UART_PIN_NO_CHANGE));

    xTaskCreate(image_receiver_task, "img_rx", 24576, NULL, 5, NULL);
    ESP_LOGI(TAG, "Ready.");
}
