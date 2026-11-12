# ĐỒ ÁN KHAI PHÁ DỮ LIỆU (ITEC3417)
## Chủ đề: Khai phá Dữ liệu Tuyển dụng ngành CNTT và Khoa học Dữ liệu tại Việt Nam

---

### 1. Cấu trúc thư mục dự án
```text
DataMining_Tool/
├── crawler/                  # Mã nguồn thu thập dữ liệu (Giai đoạn 1)
│   ├── scraper.py            # Script cào dữ liệu chính
│   ├── requirements.txt      # Thư viện phụ thuộc
│   └── README.md             # Hướng dẫn chi tiết vận hành crawler
├── data/                     # Thư mục chứa dữ liệu
│   ├── raw_data.csv          # Dữ liệu thô thu thập từ website (Bất biến)
│   ├── clean_data.csv        # Dữ liệu sạch sau tiền xử lý (Giai đoạn 2.1)
│   └── data_dictionary.xlsx  # Từ điển dữ liệu mô tả các trường
├── notebooks/                # Jupyter Notebooks thực hiện quy trình KDD
│   └── NhomXX_MiningPipeline.ipynb
├── reports/                  # Báo cáo và slide thuyết trình
│   ├── NhomXX_BaoCao_BaiTapLon.pdf
│   └── NhomXX_Slide_BaoCao.pdf
├── logs/                     # Nhật ký hoạt động của crawler
│   └── crawler.log
└── README.md
```

### 2. Tổng quan các giai đoạn thực hiện
- **Giai đoạn 1:** Xây dựng Web Crawler tự động, thu thập 800 - 1.500+ tin tuyển dụng (URL_Goc, Timestamp, Raw text).
- **Giai đoạn 2.1:** Tiền xử lý, làm sạch văn bản, tách Luong_Min / Luong_Max, chuẩn hóa địa điểm/kinh nghiệm, trích xuất từ điển kỹ năng.
- **Giai đoạn 2.2:** Phân tích khám phá dữ liệu (EDA) và trực quan hóa (Top kỹ năng, phân phối lương, khu vực).
- **Giai đoạn 2.3:** Phân cụm (K-Means / Hierarchical Clustering), đánh giá Elbow và Silhouette Score.
- **Giai đoạn 2.4:** Khai phá luật kết hợp (Association Rules) với Apriori / FP-Growth (Lift > 1.2, Support, Confidence, Conviction).
- **Giai đoạn 2.5:** Phân lớp và dự báo (Decision Tree kèm Cost-Complexity Pruning, mô hình đối sánh Random Forest/KNN/Logistic Regression).
- **Giai đoạn 3:** Viết báo cáo hoàn chỉnh (15 - 25 trang), thiết kế Slide và chuẩn bị kịch bản bảo vệ / demo trực tiếp.
