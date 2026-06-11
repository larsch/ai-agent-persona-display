#include <stdbool.h>
#include <stdint.h>
#include <string.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "driver/i2c.h"
#include "driver/i2c_master.h"
#include "driver/ledc.h"

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

#define LCD_H_RES 480
#define LCD_V_RES 480
#define LCD_BITS_PER_PIXEL 16
#define LCD_BYTES_PER_PIXEL 2

#define LCD_PIN_PCLK 21
#define LCD_PIN_DE 18
#define LCD_PIN_VSYNC 17
#define LCD_PIN_HSYNC 16

#define LCD_PIN_SPI_CS 39
#define LCD_PIN_SPI_SCL 48
#define LCD_PIN_SPI_SDA 47

#define LCD_PIN_BL 38

#define TOUCH_I2C_PORT I2C_NUM_0
#define TOUCH_PIN_SDA 19
#define TOUCH_PIN_SCL 45

#define GT911_ADDR_PRIMARY 0x5D
#define GT911_ADDR_BACKUP 0x14
#define GT911_REG_PRODUCT_ID 0x8140
#define GT911_REG_CONFIG_VERSION 0x8047
#define GT911_REG_POINT_INFO 0x814E
#define GT911_REG_POINT_1 0x814F
#define GT911_MAX_CONTACTS 5
#define TOUCH_POLL_MS 10
#define TOUCH_I2C_SPEED_HZ 100000
#define TOUCH_I2C_TIMEOUT_MS 50

#define LCD_PCLK_HZ (12 * 1000 * 1000)
#define LCD_BOUNCE_LINES 10
#define LCD_BOUNCE_PIXELS (LCD_H_RES * LCD_BOUNCE_LINES)

#define PHASE1A_SOLID_MS 1000
#define PHASE1A_BARS_MS 2000

#define PHASE1B_TEMPLATE_COUNT 12
#define PHASE1B_FRAME_HOLD_VSYNC 2
#define TOUCH_BOX_HALF_SIZE 30

#define RGB_DATA_PIN_BLUE_0 4
#define RGB_DATA_PIN_BLUE_1 5
#define RGB_DATA_PIN_BLUE_2 6
#define RGB_DATA_PIN_BLUE_3 7
#define RGB_DATA_PIN_BLUE_4 15
#define RGB_DATA_PIN_GREEN_0 8
#define RGB_DATA_PIN_GREEN_1 20
#define RGB_DATA_PIN_GREEN_2 3
#define RGB_DATA_PIN_GREEN_3 46
#define RGB_DATA_PIN_GREEN_4 9
#define RGB_DATA_PIN_GREEN_5 10
#define RGB_DATA_PIN_RED_0 11
#define RGB_DATA_PIN_RED_1 12
#define RGB_DATA_PIN_RED_2 13
#define RGB_DATA_PIN_RED_3 14
#define RGB_DATA_PIN_RED_4 0

typedef struct {
    volatile uint8_t anim_phase;
    volatile uint8_t anim_vsync_count;
    volatile uint8_t touch_points;
    volatile uint16_t touch_x[CONFIG_ESP_LCD_TOUCH_MAX_POINTS];
    volatile uint16_t touch_y[CONFIG_ESP_LCD_TOUCH_MAX_POINTS];
} runtime_state_t;

static const char *TAG = "bootstrap";

static runtime_state_t s_runtime;
static esp_lcd_panel_handle_t s_panel_phase1a;
static esp_lcd_panel_handle_t s_panel_phase1b;
static uint16_t *s_phase1b_frames[PHASE1B_TEMPLATE_COUNT];

static const uint16_t s_phase1a_palette[] = {
    0xF800, 0x07E0, 0x001F, 0xFFE0,
    0xF81F, 0x07FF, 0xFFFF, 0x0000,
};

static const uint8_t s_phase1b_wave[PHASE1B_TEMPLATE_COUNT] = {
    0, 1, 2, 3, 4, 5, 6, 5, 4, 3, 2, 1,
};

static const uint16_t s_touch_colors[GT911_MAX_CONTACTS] = {
    0xF800, // red
    0x07E0, // green
    0x001F, // blue
    0xFFE0, // yellow
    0xF81F, // magenta
};

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

static inline uint16_t rgb565(uint8_t r, uint8_t g, uint8_t b)
{
    return (uint16_t)(((r & 0xF8U) << 8) | ((g & 0xFCU) << 3) | (b >> 3));
}

static inline int IRAM_ATTR line_from_pos(int pos_px)
{
    int line = 0;

    while (pos_px >= LCD_H_RES) {
        pos_px -= LCD_H_RES;
        line++;
    }

    return line;
}

static inline void IRAM_ATTR copy_row_480(uint16_t *dst, const uint16_t *src)
{
    for (int i = 0; i < LCD_H_RES; i++) {
        dst[i] = src[i];
    }
}

static inline uint16_t phase1b_pixel_for_xy(int x, int y, int phase)
{
    uint16_t color;
    int wave_a = s_phase1b_wave[phase];
    int wave_b = s_phase1b_wave[(phase + (PHASE1B_TEMPLATE_COUNT / 3)) % PHASE1B_TEMPLATE_COUNT];
    int dx_main = x - y;
    int dx_anti = x - (LCD_H_RES - 1 - y);
    int moving_x = x + (wave_a * 3);
    int moving_y = y + (wave_b * 3);
    int vertical_bar_x = 144 + (wave_a * 12);
    int horizontal_bar_y = 144 + (wave_b * 12);
    int cell;

    if (dx_main < 0) {
        dx_main = -dx_main;
    }
    if (dx_anti < 0) {
        dx_anti = -dx_anti;
    }
    while (moving_x >= LCD_H_RES) {
        moving_x -= LCD_H_RES;
    }
    while (moving_y >= LCD_V_RES) {
        moving_y -= LCD_V_RES;
    }
    while (vertical_bar_x >= LCD_H_RES) {
        vertical_bar_x -= LCD_H_RES;
    }
    while (horizontal_bar_y >= LCD_V_RES) {
        horizontal_bar_y -= LCD_V_RES;
    }

    cell = ((moving_x / 48) ^ (moving_y / 48)) & 1;

    if ((x < 6) || (x >= (LCD_H_RES - 6)) || (y < 6) || (y >= (LCD_V_RES - 6))) {
        color = rgb565(255, 255, 255);
    } else if (dx_main <= 2 || dx_anti <= 2) {
        color = rgb565(255, 255, 255);
    } else if ((x >= 236 && x <= 244) || (y >= 236 && y <= 244)) {
        color = rgb565(255, 255, 255);
    } else if ((x >= (vertical_bar_x - 10)) && (x <= (vertical_bar_x + 10))) {
        color = rgb565(255, 32, 220);
    } else if ((y >= (horizontal_bar_y - 10)) && (y <= (horizontal_bar_y + 10))) {
        color = rgb565(32, 255, 255);
    } else if (y < 240) {
        color = (x < 240)
                    ? (cell ? rgb565(255, 96, 96) : rgb565(120, 24, 24))
                    : (cell ? rgb565(96, 255, 128) : rgb565(24, 120, 40));
    } else {
        color = (x < 240)
                    ? (cell ? rgb565(96, 160, 255) : rgb565(24, 48, 120))
                    : (cell ? rgb565(255, 224, 96) : rgb565(120, 96, 24));
    }

    return color;
}

static void prepare_phase1b_frames(void)
{
    size_t frame_size = LCD_H_RES * LCD_V_RES * LCD_BYTES_PER_PIXEL;

    for (int phase = 0; phase < PHASE1B_TEMPLATE_COUNT; phase++) {
        s_phase1b_frames[phase] = heap_caps_malloc(frame_size, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
        ESP_ERROR_CHECK(s_phase1b_frames[phase] ? ESP_OK : ESP_ERR_NO_MEM);

        for (int y = 0; y < LCD_V_RES; y++) {
            uint16_t *row = s_phase1b_frames[phase] + (y * LCD_H_RES);
            for (int x = 0; x < LCD_H_RES; x++) {
                row[x] = phase1b_pixel_for_xy(x, y, phase);
            }
        }
    }
}

static esp_err_t gt911_read_reg(uint16_t reg, uint8_t *data, size_t len)
{
    uint8_t reg_buf[2] = {(uint8_t)(reg >> 8), (uint8_t)(reg & 0xFF)};
    return i2c_master_write_read_device(TOUCH_I2C_PORT, GT911_ADDR_PRIMARY, reg_buf, sizeof(reg_buf), data, len, pdMS_TO_TICKS(TOUCH_I2C_TIMEOUT_MS));
}

static esp_err_t gt911_write_reg8(uint16_t reg, uint8_t value)
{
    uint8_t buf[3] = {(uint8_t)(reg >> 8), (uint8_t)(reg & 0xFF), value};
    return i2c_master_write_to_device(TOUCH_I2C_PORT, GT911_ADDR_PRIMARY, buf, sizeof(buf), pdMS_TO_TICKS(TOUCH_I2C_TIMEOUT_MS));
}

static bool gt911_product_id_looks_valid(const uint8_t *product_id, size_t len)
{
    bool any_nonzero = false;

    for (size_t i = 0; i < len; i++) {
        if (product_id[i] != 0x00) {
            any_nonzero = true;
        }
        if ((product_id[i] < '0') || (product_id[i] > '9')) {
            return false;
        }
    }

    return any_nonzero;
}

static esp_err_t gt911_connect(void)
{
    uint8_t product_id[3] = {0};
    uint8_t config_version = 0;
    if (gt911_read_reg(GT911_REG_PRODUCT_ID, product_id, sizeof(product_id)) != ESP_OK) {
        return ESP_FAIL;
    }
    if (gt911_read_reg(GT911_REG_CONFIG_VERSION, &config_version, 1) != ESP_OK) {
        return ESP_FAIL;
    }
    if (!gt911_product_id_looks_valid(product_id, sizeof(product_id))) {
        return ESP_FAIL;
    }

    ESP_LOGI(TAG, "GT911 addr=0x%02X product_id=%c%c%c config_version=%u",
             GT911_ADDR_PRIMARY, product_id[0], product_id[1], product_id[2], config_version);
    return ESP_OK;
}

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

static void fill_rect_rgb565(uint16_t *fb, int x0, int y0, int w, int h, uint16_t color)
{
    for (int y = 0; y < h; y++) {
        uint16_t *row = fb + ((y0 + y) * LCD_H_RES) + x0;
        for (int x = 0; x < w; x++) {
            row[x] = color;
        }
    }
}

static void draw_phase1a_color_bars(uint16_t *fb)
{
    const int bar_width = LCD_H_RES / (int)(sizeof(s_phase1a_palette) / sizeof(s_phase1a_palette[0]));

    for (int i = 0; i < (int)(sizeof(s_phase1a_palette) / sizeof(s_phase1a_palette[0])); i++) {
        int x = i * bar_width;
        int width = (i == ((int)(sizeof(s_phase1a_palette) / sizeof(s_phase1a_palette[0])) - 1)) ? (LCD_H_RES - x) : bar_width;
        fill_rect_rgb565(fb, x, 0, width, LCD_V_RES, s_phase1a_palette[i]);
    }
}

static void draw_phase1a_test_pattern(esp_lcd_panel_handle_t panel)
{
    uint16_t *frame = heap_caps_malloc(LCD_H_RES * LCD_V_RES * LCD_BYTES_PER_PIXEL, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    ESP_ERROR_CHECK(frame ? ESP_OK : ESP_ERR_NO_MEM);

    for (int i = 0; i < LCD_H_RES * LCD_V_RES; i++) {
        frame[i] = 0x001F;
    }
    ESP_ERROR_CHECK(esp_lcd_panel_draw_bitmap(panel, 0, 0, LCD_H_RES, LCD_V_RES, frame));
    vTaskDelay(pdMS_TO_TICKS(PHASE1A_SOLID_MS));

    draw_phase1a_color_bars(frame);
    ESP_ERROR_CHECK(esp_lcd_panel_draw_bitmap(panel, 0, 0, LCD_H_RES, LCD_V_RES, frame));
    vTaskDelay(pdMS_TO_TICKS(PHASE1A_BARS_MS));

    free(frame);
}

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
    esp_lcd_panel_io_3wire_spi_config_t io_config = ST7701_PANEL_IO_3WIRE_SPI_CONFIG(line_config, 0);
    esp_lcd_panel_io_handle_t io_handle = NULL;
    ESP_ERROR_CHECK(esp_lcd_new_panel_io_3wire_spi(&io_config, &io_handle));
    return io_handle;
}

static esp_lcd_rgb_panel_config_t make_rgb_config(bool no_fb)
{
    esp_lcd_rgb_panel_config_t rgb_config = {
        .clk_src = LCD_CLK_SRC_DEFAULT,
        .timings = make_panel_timing(),
        .data_width = 16,
        .in_color_format = LCD_COLOR_FMT_RGB565,
        .out_color_format = LCD_COLOR_FMT_RGB565,
        .num_fbs = no_fb ? 0 : 1,
        .bounce_buffer_size_px = no_fb ? LCD_BOUNCE_PIXELS : 0,
        .dma_burst_size = 64,
        .hsync_gpio_num = LCD_PIN_HSYNC,
        .vsync_gpio_num = LCD_PIN_VSYNC,
        .de_gpio_num = LCD_PIN_DE,
        .pclk_gpio_num = LCD_PIN_PCLK,
        .disp_gpio_num = -1,
        .data_gpio_nums = {
            RGB_DATA_PIN_BLUE_0,
            RGB_DATA_PIN_BLUE_1,
            RGB_DATA_PIN_BLUE_2,
            RGB_DATA_PIN_BLUE_3,
            RGB_DATA_PIN_BLUE_4,
            RGB_DATA_PIN_GREEN_0,
            RGB_DATA_PIN_GREEN_1,
            RGB_DATA_PIN_GREEN_2,
            RGB_DATA_PIN_GREEN_3,
            RGB_DATA_PIN_GREEN_4,
            RGB_DATA_PIN_GREEN_5,
            RGB_DATA_PIN_RED_0,
            RGB_DATA_PIN_RED_1,
            RGB_DATA_PIN_RED_2,
            RGB_DATA_PIN_RED_3,
            RGB_DATA_PIN_RED_4,
        },
        .flags = {
            .fb_in_psram = no_fb ? 0 : 1,
            .no_fb = no_fb ? 1 : 0,
        },
    };

    return rgb_config;
}

static esp_lcd_panel_handle_t new_st7701_panel_with_fb(void)
{
    esp_lcd_panel_io_handle_t io_handle = new_st7701_io();
    esp_lcd_rgb_panel_config_t rgb_config = make_rgb_config(false);
    st7701_vendor_config_t vendor_config = {
        .rgb_config = &rgb_config,
        .init_cmds = s_st7701_type9_init_ops,
        .init_cmds_size = sizeof(s_st7701_type9_init_ops) / sizeof(s_st7701_type9_init_ops[0]),
        .flags = {
            .mirror_by_cmd = 1,
        },
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

static IRAM_ATTR bool panel_on_vsync(esp_lcd_panel_handle_t panel, const esp_lcd_rgb_panel_event_data_t *edata, void *user_ctx)
{
    runtime_state_t *state = (runtime_state_t *)user_ctx;

    (void)panel;
    (void)edata;

    state->anim_vsync_count++;
    if (state->anim_vsync_count >= PHASE1B_FRAME_HOLD_VSYNC) {
        state->anim_vsync_count = 0;
        state->anim_phase++;
        if (state->anim_phase >= PHASE1B_TEMPLATE_COUNT) {
            state->anim_phase = 0;
        }
    }

    return false;
}

static IRAM_ATTR bool panel_on_bounce_empty(esp_lcd_panel_handle_t panel, void *bounce_buf, int pos_px, int len_bytes, void *user_ctx)
{
    runtime_state_t *state = (runtime_state_t *)user_ctx;
    uint16_t *dst = (uint16_t *)bounce_buf;
    const uint16_t *frame = s_phase1b_frames[state->anim_phase];
    const int lines = len_bytes / (LCD_BYTES_PER_PIXEL * LCD_H_RES);
    const int start_line = line_from_pos(pos_px);
    const uint8_t touch_points = state->touch_points;

    (void)panel;

    for (int row = 0; row < lines; row++) {
        uint16_t *row_dst = dst + (row * LCD_H_RES);
        int y = start_line + row;

        if (y >= LCD_V_RES) {
            y -= LCD_V_RES;
        }

        copy_row_480(row_dst, frame + (y * LCD_H_RES));

        for (uint8_t point = 0; point < touch_points; point++) {
            int touch_x = state->touch_x[point];
            int touch_y = state->touch_y[point];
            uint16_t touch_color = s_touch_colors[point % GT911_MAX_CONTACTS];
            int box_left = touch_x - TOUCH_BOX_HALF_SIZE;
            int box_right = touch_x + TOUCH_BOX_HALF_SIZE;
            int box_top = touch_y - TOUCH_BOX_HALF_SIZE;
            int box_bottom = touch_y + TOUCH_BOX_HALF_SIZE;

            if (box_left < 0) {
                box_left = 0;
            }
            if (box_right >= LCD_H_RES) {
                box_right = LCD_H_RES - 1;
            }
            if (box_top < 0) {
                box_top = 0;
            }
            if (box_bottom >= LCD_V_RES) {
                box_bottom = LCD_V_RES - 1;
            }

            if ((y < box_top) || (y > box_bottom)) {
                continue;
            }

            for (int x = box_left; x <= box_right; x++) {
                row_dst[x] = touch_color;
            }
        }
    }

    return false;
}

static esp_lcd_panel_handle_t new_rgb_panel_no_fb(void)
{
    esp_lcd_rgb_panel_config_t rgb_config = make_rgb_config(true);
    esp_lcd_panel_handle_t panel = NULL;

    ESP_ERROR_CHECK(esp_lcd_new_rgb_panel(&rgb_config, &panel));

    const esp_lcd_rgb_panel_event_callbacks_t callbacks = {
        .on_vsync = panel_on_vsync,
        .on_bounce_empty = panel_on_bounce_empty,
    };
    ESP_ERROR_CHECK(esp_lcd_rgb_panel_register_event_callbacks(panel, &callbacks, &s_runtime));

    return panel;
}

static void send_st7701_init_only(void)
{
    esp_lcd_panel_io_handle_t io_handle = new_st7701_io();
    esp_lcd_rgb_panel_config_t rgb_config = make_rgb_config(true);
    st7701_vendor_config_t vendor_config = {
        .rgb_config = &rgb_config,
        .init_cmds = s_st7701_type9_init_ops,
        .init_cmds_size = sizeof(s_st7701_type9_init_ops) / sizeof(s_st7701_type9_init_ops[0]),
        .flags = {
            .enable_io_multiplex = 1,
        },
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
    ESP_ERROR_CHECK(esp_lcd_panel_del(panel));
}

static void init_touch(void)
{
    const i2c_config_t i2c_conf = {
        .mode = I2C_MODE_MASTER,
        .sda_io_num = TOUCH_PIN_SDA,
        .sda_pullup_en = GPIO_PULLUP_ENABLE,
        .scl_io_num = TOUCH_PIN_SCL,
        .scl_pullup_en = GPIO_PULLUP_ENABLE,
        .master.clk_speed = TOUCH_I2C_SPEED_HZ,
    };
    ESP_ERROR_CHECK(i2c_param_config(TOUCH_I2C_PORT, &i2c_conf));
    ESP_ERROR_CHECK(i2c_driver_install(TOUCH_I2C_PORT, i2c_conf.mode, 0, 0, 0));

    ESP_ERROR_CHECK(gt911_connect());
}

static void touch_task(void *arg)
{
    (void)arg;

    while (true) {
        uint8_t point_info = 0;
        uint8_t point_buf[GT911_MAX_CONTACTS * 8] = {0};
        uint8_t count = 0;

        if (gt911_read_reg(GT911_REG_POINT_INFO, &point_info, 1) == ESP_OK) {
            if ((point_info & 0x80U) && ((point_info & 0x0FU) > 0U)) {
                count = point_info & 0x0FU;
                if (count > GT911_MAX_CONTACTS) {
                    count = GT911_MAX_CONTACTS;
                }
                if (count > CONFIG_ESP_LCD_TOUCH_MAX_POINTS) {
                    count = CONFIG_ESP_LCD_TOUCH_MAX_POINTS;
                }

                if (gt911_read_reg(GT911_REG_POINT_1, point_buf, count * 8) == ESP_OK) {
                    for (uint8_t i = 0; i < count; i++) {
                        uint16_t x = (uint16_t)point_buf[(i * 8) + 1] | ((uint16_t)point_buf[(i * 8) + 2] << 8);
                        uint16_t y = (uint16_t)point_buf[(i * 8) + 3] | ((uint16_t)point_buf[(i * 8) + 4] << 8);

                        s_runtime.touch_x[i] = (x < LCD_H_RES) ? x : (LCD_H_RES - 1);
                        s_runtime.touch_y[i] = (y < LCD_V_RES) ? y : (LCD_V_RES - 1);
                    }
                    s_runtime.touch_points = count;
                } else {
                    s_runtime.touch_points = 0;
                }
            } else {
                s_runtime.touch_points = 0;
            }

            ESP_ERROR_CHECK(gt911_write_reg8(GT911_REG_POINT_INFO, 0));
        } else {
            s_runtime.touch_points = 0;
        }

        vTaskDelay(pdMS_TO_TICKS(TOUCH_POLL_MS));
    }
}

void app_main(void)
{
    ESP_LOGI(TAG, "Bootstrapping Guition ESP32-S3-4848S040");
    configure_backlight();

    ESP_LOGI(TAG, "Phase 1a: ST7701 framebuffer validation");
    s_panel_phase1a = new_st7701_panel_with_fb();
    draw_phase1a_test_pattern(s_panel_phase1a);

    ESP_LOGI(TAG, "Phase 1b: bounce-buffer-only procedural rendering");
    ESP_ERROR_CHECK(esp_lcd_panel_del(s_panel_phase1a));
    s_panel_phase1a = NULL;

    prepare_phase1b_frames();
    send_st7701_init_only();
    s_panel_phase1b = new_rgb_panel_no_fb();
    ESP_ERROR_CHECK(esp_lcd_panel_reset(s_panel_phase1b));
    ESP_ERROR_CHECK(esp_lcd_panel_init(s_panel_phase1b));
    ESP_LOGI(TAG, "Phase 1b active with animated background");

    ESP_LOGI(TAG, "Phase 2: GT911 touch overlay");
    init_touch();
    xTaskCreate(touch_task, "touch_task", 4096, NULL, 5, NULL);
    ESP_LOGI(TAG, "Touch active; drawing crosses for up to %d contacts", CONFIG_ESP_LCD_TOUCH_MAX_POINTS);
}
