# ============================================================================
#  BloomAnalytics · R 可视化脚本
#
#  用途：用 ggplot2 复刻 4 张核心图，验证"同一份数据、不同工具、结论一致"。
#  依赖：ggplot2、dplyr、readr、scales（可选 showtext 解决中文字体）
#  运行：setwd("10_BI工具配套_20261121"); source("04_R可视化脚本.R")
#  输出：r_output/*.png
#
#  说明：本机未安装 R，此脚本按 R 4.x + tidyverse 语法编写，
#        在有 R 的环境里可直接运行；字段名与 data/ 下 CSV 一致。
# ============================================================================

library(ggplot2)
library(dplyr)
library(readr)
library(scales)

data_dir  <- "data"
out_dir   <- "r_output"
dir.create(out_dir, showWarnings = FALSE)

# 中文字体：Windows 用微软雅黑；若显示方块，安装 showtext 后取消下面两行注释
# library(showtext); showtext_auto()
font_family <- "Microsoft YaHei"

theme_bloom <- function() {
  theme_minimal(base_family = font_family, base_size = 12) +
    theme(
      plot.title      = element_text(face = "bold", size = 14, margin = margin(b = 8)),
      plot.subtitle   = element_text(color = "#5b6880", size = 10, margin = margin(b = 12)),
      panel.grid.minor = element_blank(),
      panel.grid.major = element_line(color = "#eef1f6"),
      axis.title      = element_text(color = "#5b6880", size = 10),
      legend.position = "top",
      plot.background = element_rect(fill = "white", color = NA)
    )
}

# ---------------------------------------------------------------------------
# 图 1：月度 GMV 趋势（按年份分色）
# ---------------------------------------------------------------------------
kpi <- read_csv(file.path(data_dir, "ads_kpi_monthly.csv"), show_col_types = FALSE) %>%
  mutate(year = substr(month_key, 1, 4),
         month = as.integer(substr(month_key, 6, 7)))

p1 <- ggplot(kpi, aes(x = month, y = gmv / 1e4, fill = year)) +
  geom_col(position = "dodge", width = 0.72) +
  scale_x_continuous(breaks = 1:12, labels = paste0(1:12, "月")) +
  scale_fill_manual(values = c("2024" = "#b9c6e0", "2025" = "#7f9cf5", "2026" = "#2547c4")) +
  labs(title = "月度 GMV：2 月与 5 月是双峰",
       subtitle = "单位：万元｜数据周期 2024-01 ~ 2026-11",
       x = NULL, y = "GMV（万元）", fill = NULL) +
  theme_bloom()

ggsave(file.path(out_dir, "01_monthly_gmv.png"), p1, width = 9, height = 4.2, dpi = 150)

# ---------------------------------------------------------------------------
# 图 2：RFM 八分群（人数 vs 消费额）
# ---------------------------------------------------------------------------
seg <- read_csv(file.path(data_dir, "ads_customer_rfm_segments.csv"), show_col_types = FALSE) %>%
  mutate(cust_share = customers / sum(customers),
         gmv_share  = gmv / sum(gmv),
         rfm_segment = reorder(rfm_segment, -gmv_share))

p2 <- seg %>%
  tidyr::pivot_longer(c(cust_share, gmv_share), names_to = "指标", values_to = "占比") %>%
  mutate(指标 = recode(指标, cust_share = "人数占比", gmv_share = "消费额占比")) %>%
  ggplot(aes(x = rfm_segment, y = 占比, fill = 指标)) +
  geom_col(position = "dodge", width = 0.7) +
  scale_y_continuous(labels = percent_format(accuracy = 1)) +
  scale_fill_manual(values = c("人数占比" = "#9aa7bd", "消费额占比" = "#2547c4")) +
  coord_flip() +
  labs(title = "RFM 八分群：人多的群不一定贡献大",
       subtitle = "消费额占比越高于人数占比，说明该群体越值钱",
       x = NULL, y = NULL, fill = NULL) +
  theme_bloom()

ggsave(file.path(out_dir, "02_rfm_segments.png"), p2, width = 8.5, height = 4.6, dpi = 150)

# ---------------------------------------------------------------------------
# 图 3：渠道效率（客单价 × 净利率，气泡 = GMV）
# ---------------------------------------------------------------------------
ch <- read_csv(file.path(data_dir, "ads_channel_efficiency.csv"), show_col_types = FALSE)
ch$commission_rate <- ch$commission_amt / ch$gmv

p3 <- ggplot(ch, aes(x = aov, y = net_margin, size = gmv, color = channel_type)) +
  geom_point(alpha = 0.75) +
  geom_text(aes(label = channel_name), vjust = -1.2, size = 3.2, family = font_family) +
  scale_size_continuous(labels = unit_format(unit = "万", scale = 1e-4), range = c(4, 16)) +
  scale_color_manual(values = c("线上" = "#2547c4", "线下" = "#0f9d63")) +
  scale_y_continuous(labels = percent_format(accuracy = 1)) +
  labs(title = "渠道效率：客单价 × 佣金后净利率",
       subtitle = "气泡大小 = GMV；右下角是高客单但被佣金吃掉的渠道",
       x = "客单价（元）", y = "佣金后净利率", size = "GMV", color = "渠道类型") +
  theme_bloom()

ggsave(file.path(out_dir, "03_channel_efficiency.png"), p3, width = 8.5, height = 5, dpi = 150)

# ---------------------------------------------------------------------------
# 图 4：库存周转 vs 损耗率（双轴）
# ---------------------------------------------------------------------------
inv <- read_csv(file.path(data_dir, "ads_inventory_analysis.csv"), show_col_types = FALSE) %>%
  arrange(desc(waste_amt)) %>%
  mutate(category = factor(category, levels = category))

coef <- max(inv$turnover_days) / max(inv$waste_rate)   # 让两条轴量级接近

p4 <- ggplot(inv, aes(x = category)) +
  geom_col(aes(y = turnover_days), fill = "#2547c4", width = 0.62, alpha = 0.85) +
  geom_line(aes(y = waste_rate * coef, group = 1), color = "#d64545", linewidth = 1.1) +
  geom_point(aes(y = waste_rate * coef), color = "#d64545", size = 2.4) +
  scale_y_continuous(
    name = "周转天数（天）",
    sec.axis = sec_axis(~ . / coef, name = "损耗率", labels = percent_format(accuracy = 0.1))
  ) +
  labs(title = "品类周转与损耗",
       subtitle = "柱 = 周转天数，红线 = 损耗率；柱高且线高的品类是重点整改对象",
       x = NULL) +
  theme_bloom() +
  theme(axis.text.x = element_text(angle = 30, hjust = 1))

ggsave(file.path(out_dir, "04_inventory.png"), p4, width = 9, height = 4.6, dpi = 150)

cat("✓ 4 张图已输出到", out_dir, "\n")
