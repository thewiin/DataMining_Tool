# HƯỚNG DẪN THỰC THI MULTI-SOURCE WEB CRAWLER (GIAI ĐOẠN 1)
**Học phần:** Khai phá Dữ liệu (ITEC3417)  
**Chủ đề:** Khai phá dữ liệu tuyển dụng ngành CNTT và Khoa học dữ liệu tại Việt Nam  
**3 Nền tảng mục tiêu:** **TopCV**, **Joboko**, **ITviec**

---

## 1. Yêu cầu Môi trường & Cài đặt Thư viện

Khuyến nghị sử dụng **Python 3.10+**.

### Bước 1: Kích hoạt môi trường ảo (Nếu chưa kích hoạt)
```powershell
# Mở terminal tại thư mục gốc DataMining_Tool
.venv\Scripts\Activate.ps1
```

### Bước 2: Cài đặt thư viện cần thiết
```powershell
pip install -r crawler/requirements.txt
```
*(Bao gồm: `requests`, `beautifulsoup4`, `lxml`, `pandas`, `tqdm`)*

---

## 2. Hướng dẫn Chạy Thu thập Dữ liệu

### Chế độ 1: Chạy kiểm thử nhanh (Dry-Run Test)
Quét thử 2 tin từ mỗi nguồn được chọn (`TopCV`, `Joboko`, `ITviec`) để kiểm tra bộ parser và kết nối mạng:
```powershell
python crawler/scraper.py --test
```

### Chế độ 2: Chạy đầy đủ cả 3 nguồn (Khuyến nghị đạt 800 - 1500+ tin)
Hệ thống sẽ tự động phân bổ định mức đều nhau cho 3 website (ví dụ 1200 tin tổng chia cho 3 nguồn $\approx$ 400 tin/nguồn):
```powershell
python crawler/scraper.py --sources topcv,joboko,itviec --limit 1200 --max-pages 15
```

### Chế độ 3: Chạy riêng lẻ hoặc bổ sung từng nguồn
* **Chỉ cào TopCV:**
  ```powershell
  python crawler/scraper.py --sources topcv --limit 500
  ```
* **Chỉ cào Joboko:**
  ```powershell
  python crawler/scraper.py --sources joboko --limit 500
  ```
* **Chỉ cào ITviec:**
  ```powershell
  python crawler/scraper.py --sources itviec --limit 500
  ```

### Chế độ 4: Tùy chỉnh bộ từ khóa
```powershell
python crawler/scraper.py --sources topcv,joboko,itviec --keywords "Data Analyst,Data Engineer,Data Scientist,AI Engineer,Machine Learning" --limit 1500
```

---

## 3. Cấu trúc Schema Dữ liệu Thô (`data/raw_data.csv`)

Dữ liệu thô được bảo toàn nguyên vẹn 100% với 12 trường:
1. `Job_ID`: Mã tin tuyển dụng từ trang nguồn.
2. `Job_Title`: Tên vị trí tuyển dụng nguyên bản.
3. `Company`: Tên công ty / doanh nghiệp.
4. `Location_Raw`: Địa điểm nguyên bản trên trang (VD: *"Hồ Chí Minh"*, *"Hà Nội"*).
5. `Salary_Raw`: Mức lương nguyên bản (VD: *"15 - 25 triệu"*, *"1000$ - 2000$"*, *"Thỏa thuận"*).
6. `Experience_Raw`: Kinh nghiệm yêu cầu nguyên bản (VD: *"1 - 2 năm"*, *"Không yêu cầu"*).
7. `Description_Raw`: Văn bản mô tả công việc (JD) đầy đủ.
8. `Requirements_Raw`: Yêu cầu ứng viên / kỹ năng.
9. `Benefits_Raw`: Chế độ đãi ngộ / quyền lợi.
10. `URL_Goc`: Link gốc trực tiếp đến tin đăng (phục vụ giảng viên audit).
11. `Timestamp`: Thời điểm crawler thu thập dữ liệu (`YYYY-MM-DD HH:MM:SS`).
12. `Source`: Tên nguồn (`TopCV`, `Joboko`, `ITviec`).

---

## 4. Các tính năng Nổi bật
- **Khử trùng & Resume tự động:** Tự động đọc lại các `URL_Goc` đã có trong file CSV cũ để bỏ qua khi chạy tiếp. Bạn có thể bấm `Ctrl + C` bất kỳ lúc nào để tạm dừng và chạy lại lệnh mà không lo bị trùng dữ liệu.
- **Lưu lũy tiến (Append Checkpoint):** Từng bản ghi được lưu ngay lập tức vào đĩa, phòng tránh rủi ro mất mạng hoặc mất điện đột ngột.
- **Chống chặn IP:** Random delay 1.5s – 3.0s, xoay tua User-Agent, Exponential Backoff khi gặp HTTP 429/503.
- **Báo cáo nghiệm thu tự động:** Tự động in bảng phân loại số lượng tin theo từng nguồn (`Source`), số URL duy nhất và tỷ lệ dữ liệu khuyết.
