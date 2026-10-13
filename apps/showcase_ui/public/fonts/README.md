# 本地页面字体

`material-symbols-outlined.woff2` 来自 Google 官方 Material Design Icons 仓库，采用 Apache License 2.0，许可证保存在同目录的 `LICENSE-material-symbols.txt`。

来源：https://github.com/google/material-design-icons/blob/master/variablefont/MaterialSymbolsOutlined%5BFILL%2CGRAD%2Copsz%2Cwght%5D.woff2

本地提供完整可变字体，保留现有图标名称及字号、粗细、填充等设置，避免 Google 字体服务不可用时显示英文图标名称。更新字体时需同时核对许可证和现有图标名称的兼容性。

正文字体来自 Google Fonts 官方仓库，采用 SIL Open Font License 1.1，各自的许可证保存在同目录的 `LICENSE-inter.txt`、`LICENSE-outfit.txt`、`LICENSE-roboto.txt`。

- Inter：https://github.com/google/fonts/blob/main/ofl/inter/Inter%5Bopsz%2Cwght%5D.ttf
- Outfit：https://github.com/google/fonts/blob/main/ofl/outfit/Outfit%5Bwght%5D.ttf
- Roboto：https://github.com/google/fonts/blob/main/ofl/roboto/Roboto%5Bwdth%2Cwght%5D.ttf

正文使用本地字体和原有系统字体回退，避免远程字体 CSS 导入延迟影响全局样式生效。中文继续由系统字体提供。
