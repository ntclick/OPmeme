# ✅ CoinCheckGo - Fix i18n & Ngôn ngữ
**Date:** Feb 22, 2026  
**Session:** Language Audit & Fix

---

## 🎯 Các Vấn Đề Đã Sửa

### 1. ✅ Lẫn lộn tiếng Anh/Việt trên Frontend (index.html)
**Priority:** CAO  
**Status:** ĐÃ SỬA (FIXED)

**Vấn đề:**
- Các text trên trang web đang hiển thị lộn xộn giữa tiếng Anh và tiếng Việt.
- Yêu cầu triệt để sử dụng tiếng Việt làm mặc định và tự động chuyển đổi.

**Giải pháp:**
- Triển khai hệ thống `i18n` nhỏ bằng JavaScript trực tiếp trong `index.html`.
- Định nghĩa đối tượng `translations` chứa các biến dịch cho hai ngôn ngữ `en` (Anh) và `vi` (Việt).
- Tạo hàm `applyLanguage()` và `toggleLanguage()` để cập nhật text toàn bộ trang.
- Đặt `currentLang = 'vi'` làm mặc định.
- Thêm nút Toggle Ngôn ngữ ở thanh điều hướng (`🇻🇳 VN` / `🇺🇸 EN`).

### 2. ✅ Lẫn lộn ngôn ngữ từ Backend API
**Priority:** CAO  
**Status:** ĐÃ SỬA (FIXED)

**Vấn đề:**
- Các fallback messages, verdict và flags từ OpenGradient AI khi lỗi đang trả về bằng tiếng Anh, gây ra hiện tượng hiển thị lẫn lộn trên UI tiếng Việt.

**Giải pháp:**
- Cập nhật hàm `_default_result` trong `analysis/opengradient.py`.
- Dịch các thông báo lỗi và fallback verdicts sang tiếng Việt:
  - `"AI analysis failed..."` -> `"Phân tích AI thất bại..."`
  - Cập nhật các text cho `large_cap`, `established`, `new`, và `unknown`.
- Cập nhật `analysis/scoring.py` để UI mapping có thể xử lý, hoặc giữ nguyên emoji. UI hiện tại ánh xạ mảng các flags, nên việc dịch `No critical issues found` -> `Không tìm thấy vấn đề nghiêm trọng` đã được xử lý ở bước frontend phía trên.

---

## ⚠️ Vấn đề tồn đọng (Không liên quan đến ngôn ngữ)

1. **OpenGradient AI vẫn báo lỗi `[Errno 11001] getaddrinfo failed`**:
   - Đây là lỗi DNS/Mạng từ phía SDK của OpenGradient không phân giải được host.
   - Script `test_og.py` đã xác nhận lỗi này.
   - Hệ thống hiện tại đang sử dụng Fallback Scoring (Tiếng Việt) để xử lý một cách an toàn mà không làm sập ứng dụng.

---

## 🎯 Kết luận
- **Toàn bộ UI và fallback backend đã được đồng bộ 100% tiếng Việt làm mặc định.**
- Người dùng có thể dễ dàng chuyển sang tiếng Anh thông qua nút chuyển đổi ở menu góc trên bên phải.
