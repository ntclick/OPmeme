# CoinCheckGo Frontend Features Proposal

**Mục tiêu:** Xây dựng giao diện hiện đại, tối giản nhưng "ngầu" (Crypto Degen vibes), tập trung vào hiển thị điểm số và cảnh báo rủi ro một cách trực quan nhất.

## 1. Tech Stack (Đề Xuất)
*   **Framework:** **Next.js 14** (App Router) - Tối ưu SEO và tốc độ.
*   **Styling:** **Tailwind CSS** + **shadcn/ui** (Component library đẹp, chuẩn enterprise).
*   **Icons:** Lucide React.
*   **Charts:** Recharts (Hiển thị biểu đồ phân bổ ví).
*   **Hệu ứng:** Framer Motion (Animation mượt mà khi load điểm số).

## 2. Các Màn Hình Chính

### A. Trang Chủ (Landing Page)
*   **Hero Section:** Tiêu đề lớn "CoinCheckGo - Scan Meme Coins, Detect Scams".
*   **Search Bar (Trung tâm):** Ô nhập liệu to, hỗ trợ nhập Ticker ($PEPE) hoặc Contract Address.
*   **Trending Bar:** Chạy ngang hiển thị các coin đang HOT (lấy từ API `/api/trending`).
*   **Call to Action:** Nút "Check Now" nổi bật.

### B. Trang Kết Quả Phân Tích (Analysis Dashboard)
Đây là trang quan trọng nhất, hiển thị sau khi user bấm Check.

**Layout chia làm 4 khối:**

1.  **Trust Score Header (Trên cùng)**
    *   Tên Coin + Logo + Ticker.
    *   **Đồng hồ điểm số (Gauge Chart)**: Từ 0-100.
        *   0-20: Đỏ rực (SCAM/RUG).
        *   21-60: Cam/Vàng (RISKY).
        *   81-100: Xanh lá (SAFEish).
    *   **Verdict (Phán quyết)**: Một câu ngắn gọn từ AI (VD: "Con này community mạnh, dev đã bỏ quyền, chơi được!").

2.  **Chi tiết Điểm số (Grid 2x2)**
    *   **Social Score:** Icon Twitter, hiển thị độ nhiệt.
    *   **Bot Score:** Icon Robot, cảnh báo nếu bot nhiều.
    *   **Holder Score:** Icon Ví, cảnh báo cá voi.
    *   **Sentiment Score:** Icon Cảm xúc (Mặt cười/Mếu).

3.  **Red Flags & Green Flags (Cột bên trái/phải)**
    *   Danh sách các cảnh báo (Red Flags - Có dấu X đỏ): "Chưa renounce contract", "Top 10 giữ 90% supply".
    *   Danh sách điểm tốt (Green Flags - Dấu tích xanh): "Liquidity đã khóa", "KOL xịn follow".

4.  **AI Verification (Dưới cùng)**
    *   Hộp thoại hiển thị "Verified by OpenGradient".
    *   Link tới transaction hash trên explorer (Chứng minh kết quả không bị sửa).

### C. Trang Lịch Sử (History Page)
*   Danh sách các lần check gần đây của cộng đồng.
*   Bộ lọc: Xem các coin điểm cao (Hidden Gems) hoặc các coin điểm thấp (Scam warning).

---

## 3. User Experience (UX)
*   **Loading State:** Khi đang scan (mất 10-20s), hiển thị các bước đang chạy: "Scanning Twitter...", "Checking On-chain...", "Detecting Bots...".
*   **Dark Mode:** Mặc định là Dark Mode (giao diện đen/tím neon) phù hợp với dân crypto.
*   **Mobile First:** Tối ưu hoàn toàn cho điện thoại (dân meme coin toàn dùng điện thoại).

## 4. Kế Hoạch Triển Khai (Dự kiến)
1.  **Setup Next.js Project** & Cài đặt UI Library.
2.  **Xây dựng API Client:** Kết nối tới Backend Python hiện tại.
3.  **Code Trang chủ & Search:** Đảm bảo tìm kiếm mượt.
4.  **Code Trang Kết quả:** Hiển thị điểm số và biểu đồ.
5.  **Polish:** Thêm hiệu ứng, dark mode chuẩn.
