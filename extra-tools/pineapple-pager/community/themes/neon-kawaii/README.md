# Neon Kawaii Theme

**Author**: Annalea L
**Version**: 1.0.0
**Developed for**: WiFi Pineapple Pager Firmware 1.0.4+

**Description**: Neon cyber-kawaii theme with deep plum surfaces, hot-pink/violet accents, cyan highlights, and subtle sparkle energy. A complete transformation of the mayhem-red theme with 156 recolored assets and full neon aesthetic.

---

## 🎨 Color Palette

### Primary Colors
| Role | Color Name | Hex | RGB | Usage |
|------|------------|-----|-----|-------|
| **Primary** | Hot Pink | `#FF4FD8` | `255, 79, 216` | Main accent, selected items, highlights |
| **Secondary** | Violet | `#9B5CFF` | `155, 92, 255` | Secondary accents, decorative elements |
| **Info/Link** | Cyan | `#5AD7FF` | `90, 215, 255` | Information, blue states, links |
| **Highlight** | Lavender | `#E6C8FF` | `230, 200, 255` | Soft highlights, light accents |

### Semantic Colors
| Role | Color Name | Hex | RGB | Usage |
|------|------------|-----|-----|-------|
| **Success** | Mint | `#37E6A6` | `55, 230, 166` | Success states, confirmations |
| **Warning** | Gold | `#FFC857` | `255, 200, 87` | Warnings, caution states |
| **Danger** | Pink-Red | `#FF3B6B` | `255, 59, 107` | Errors, critical alerts |

### Surface & Text Colors
| Role | Color Name | Hex | RGB | Usage |
|------|------------|-----|-----|-------|
| **Base Background** | Deep Plum | `#0B0614` | `11, 6, 20` | Main background |
| **Surface 1** | Dark Plum | `#12081F` | `18, 8, 31` | Elevated surfaces |
| **Surface 2** | Mid Plum | `#1C0F2E` | `28, 15, 46` | Card backgrounds |
| **Surface 3** | Light Plum | `#2A1445` | `42, 20, 69` | Hover states |
| **Border** | Purple Border | `#3A1F5D` | `58, 31, 93` | Borders, dividers |
| **Text Primary** | Soft White | `#FFF3FB` | `255, 243, 251` | Primary text |
| **Text Secondary** | Lavender Grey | `#E7D7F4` | `231, 215, 244` | Secondary text |
| **Muted/Disabled** | Purple Grey | `#7E6A93` | `126, 106, 147` | Disabled elements |

---

## 📦 Installation

### Method 1: Direct Copy
1. Copy the entire `neon-kawaii` folder to your Pager:
   ```bash
   scp -r themes/neon-kawaii/ root@172.16.42.1:/root/themes/
   ```

2. Or if using SD card:
   ```bash
   scp -r themes/neon-kawaii/ root@172.16.42.1:/mmc/root/themes/
   ```

3. Reboot or reload themes from the Pager UI

### Method 2: Git Clone
```bash
ssh root@172.16.42.1
cd /root/
git clone https://github.com/hak5/wifipineapplepager-themes.git
cp -r wifipineapplepager-themes/themes/neon-kawaii /root/themes/
```

---

## 🔧 Customization

### Adjusting Color Intensity
Edit `theme.json` and modify the `color_palette` RGB values. The theme uses palette references throughout, so changes propagate automatically.

### Recoloring Additional Assets
The theme includes a recoloring utility script in `tools/recolor_neon_kawaii.py` (if building from source).

To adjust the fuzzy color matching threshold:
1. Open the script
2. Find line with `fuzzy_threshold=22`
3. Increase for stricter matching (fewer pixels), decrease for broader matching (more pixels)
4. Re-run the script

### Semantic Color Rules
The recoloring script uses filename patterns:
- `*error*`, `*critical*` → Danger color (#FF3B6B)
- `*warn*`, `*warning*` → Warning color (#FFC857)
- `*blue*`, `*info*` → Info/Cyan (#5AD7FF)
- `*disabled*` → Muted grey (#7E6A93)
- Default → Primary Hot Pink (#FF4FD8)

---

## 📊 Theme Statistics

- **Total PNG Assets**: 156 files
- **JSON Components**: 26 files
- **Subdirectories**: 15 asset categories
- **Color Palette Entries**: 28 defined colors
- **Base Theme**: mayhem-red (reskinned)

---

## 🎯 Design Philosophy

**Neon Kawaii** embraces the cyber-kawaii aesthetic with:
- **Deep, rich backgrounds** (plum/purple tones) for outdoor readability
- **High-contrast neon accents** (hot pink, cyan, violet) for visibility
- **Semantic color coding** (danger, warning, success) for instant recognition
- **Preserved luminance** in recolored assets to maintain depth and anti-aliasing
- **Accessibility-focused** contrast ratios for text and UI elements

---

## 🐛 Known Limitations

- Optimized for Pager firmware 1.0.4 - compatibility with other versions not guaranteed
- Some QR code assets (`help_qr.png`, `license_qr.png`) retain original colors (intentional)
- Very dark PNG assets may show minimal color change due to luminance preservation

---

## 🤝 Contributing

Found an asset that could be improved? Want to adjust a color?

1. Fork the repository
2. Make your changes
3. Test on real hardware
4. Submit a PR with screenshots

---

## 📜 Credits

**Original Base Theme**: mayhem-red by glitter-byte
**Neon Kawaii Theme**: Annalea L
**Palette Design**: Cyber-kawaii aesthetic with neon emphasis
**Recoloring Method**: Fuzzy HSV-based color transformation

---

## ⚖️ License

This theme is subject to the Hak5 Software License Agreement:
https://hak5.org/license

WiFi Pineapple is a trademark of Hak5 LLC.

---

## 📸 Screenshots

[Screenshots would be inserted here after testing on hardware]

---

**Enjoy your neon cyber-kawaii WiFi Pineapple Pager experience! 💖✨🔮**
