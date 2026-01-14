# YouTube Download Errors - Troubleshooting Guide

## 🔴 Common Error Messages

### 1. "SABR streaming" Warning
```
WARNING: Some web client https formats have been skipped as they are missing a url. 
YouTube is forcing SABR streaming for this client.
```

**Ý nghĩa:** YouTube đang ép dùng định dạng streaming mới (SABR), khiến một số URL format bị thiếu.

**Giải pháp:**
- ✅ Import cookies từ browser
- ✅ App tự động dùng ios/android client khi có cookies (bypass SABR)
- ✅ Cập nhật yt-dlp: `pip install -U yt-dlp`

---

### 2. "n challenge solving failed"
```
WARNING: n challenge solving failed: Some formats may be missing. 
Ensure you have a supported JavaScript runtime installed.
```

**Ý nghĩa:** Không giải được mã bảo vệ "n-signature" của YouTube. Cần JavaScript runtime (Node.js).

**Giải pháp:**

#### Cách 1: Cài Node.js (Khuyên dùng)
1. Tải Node.js: https://nodejs.org/ (phiên bản LTS)
2. Cài đặt Node.js
3. Khởi động lại app
4. Retry downloads

#### Cách 2: Import Cookies (Đơn giản hơn)
1. Export cookies từ browser (xem `COOKIE_GUIDE.md`)
2. Click "🍪 Import Cookie" trong app
3. App tự động dùng ios/android client (không cần giải n-signature)
4. Click "🔄 Retry Fail" để thử lại

---

### 3. "Only images are available for download"
```
WARNING: Only images are available for download. use --list-formats to see them
ERROR: Requested format is not available.
```

**Ý nghĩa:** 
- Video bị hạn chế (age-restricted, members-only, geo-blocked)
- Hoặc yt-dlp không thể lấy video formats

**Giải pháp:**
1. **Import cookies** (quan trọng nhất!)
   - Export từ browser đã đăng nhập YouTube
   - Click "🍪 Import Cookie"

2. **Giảm chất lượng**
   - Đổi Quality từ 1080p → 720p hoặc 480p
   - Click "🔄 Retry Fail"

3. **Cập nhật yt-dlp**
   ```bash
   pip install -U yt-dlp
   ```

4. **Kiểm tra video**
   - Xem video có yêu cầu đăng nhập không?
   - Có phải members-only content?
   - Có bị chặn khu vực (geo-blocked)?

---

### 4. "Requested format is not available"
```
ERROR: Requested format is not available. Use --list-formats for a list of available formats
```

**Ý nghĩa:** Format chất lượng bạn chọn không có sẵn cho video này.

**Giải pháp:**
1. Giảm chất lượng (1080p → 720p → 480p)
2. Import cookies để mở khóa thêm formats
3. App tự động retry với format "best" (chất lượng cao nhất có sẵn)

---

## 🔧 Quy trình Fix Tổng hợp

### Bước 1: Import Cookies (Quan trọng nhất!)
```
1. Mở Chrome/Firefox/Edge
2. Đăng nhập YouTube
3. Cài extension "Get cookies.txt LOCALLY"
4. Export cookies.txt
5. Click "🍪 Import Cookie" trong app
```

### Bước 2: Cài Node.js (Tùy chọn, nhưng tốt)
```
1. Tải: https://nodejs.org/
2. Cài đặt (chọn "Add to PATH")
3. Khởi động lại app
```

### Bước 3: Cập nhật yt-dlp
```bash
pip install -U yt-dlp
# Hoặc dùng nightly version (mới nhất):
pip install -U yt-dlp-nightly
```

### Bước 4: Retry Downloads
```
1. Click "🔄 Retry Fail" trong app
2. Click "▶ Start" để tải lại
```

---

## 🚀 Cơ chế Retry của App

App tự động thử nhiều cách khi download fail:

1. **Lần 1:** Thử format gốc (1080p, 720p, etc.)
2. **Lần 2 (TikTok):** Format đơn giản "best"
3. **Lần 3 (Facebook):** Bỏ HD requirement
4. **Lần 4 (Reddit):** Generic extractor
5. **Lần 5 (YouTube + Cookie):** ios/android client (bypass nsig/SABR)
6. **Lần 6 (YouTube):** Format "best"
7. **Lần 7:** Re-encode H.264/AAC

---

## 📊 Strategy với Cookies

| Trường hợp | Strategy | Bypass được |
|-----------|----------|-------------|
| **Có Cookie** | ios/android client | ✅ SABR, ✅ nsig, ✅ age-restrict |
| **Không Cookie** | web client | ❌ SABR, ❌ nsig, ❌ age-restrict |

**Kết luận:** Import cookies = giải quyết 80% lỗi YouTube!

---

## ⚠️ Các trường hợp KHÔNG fix được

Một số video **không thể tải** dù có cookies:

1. **Members-only content** 
   - Cần membership của channel đó
   - Cookie phải từ tài khoản có membership

2. **Private videos**
   - Chỉ chủ sở hữu mới xem được

3. **Livestream đang live**
   - Chỉ tải được sau khi stream kết thúc

4. **Geo-blocked content**
   - Bị chặn theo khu vực
   - Cần VPN để bypass

---

## 🎯 Checklist Debug

Khi gặp lỗi, check theo thứ tự:

- [ ] Đã import cookies chưa?
- [ ] Cookies còn hạn không? (export lại mỗi vài tháng)
- [ ] Đã cài Node.js chưa?
- [ ] Đã cập nhật yt-dlp chưa?
- [ ] Video có yêu cầu đặc biệt không? (members-only, age-restrict)
- [ ] Thử giảm chất lượng (720p, 480p)
- [ ] Check Logs tab để xem lỗi chi tiết

---

## 📞 Liên hệ Support

Nếu vẫn không fix được:
1. Check tab **Logs** trong app
2. Copy toàn bộ error message
3. Screenshot giao diện
4. Email: hungse17002@gmail.com

---

## 🔗 Links hữu ích

- yt-dlp GitHub: https://github.com/yt-dlp/yt-dlp
- Node.js Download: https://nodejs.org/
- Chrome Cookie Extension: https://chrome.google.com/webstore/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc
- yt-dlp nsig/SABR issue: https://github.com/yt-dlp/yt-dlp/issues/12482
