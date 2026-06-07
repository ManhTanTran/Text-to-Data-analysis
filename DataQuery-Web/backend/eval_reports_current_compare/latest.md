# DataQuery Web Evaluation

- Created: 2026-06-07T12:23:57
- Provider: `deepseek`
- Data: `built-in sales fixture`
- Total score: **92.56/100**

## Category Scores

- `execution`: 30.0/30 (100.00%)
- `result`: 26.81/30 (89.38%)
- `value`: 17.0/20 (85.00%)
- `chart`: 8.75/10 (87.50%)
- `stability`: 10.0/10 (100.00%)

## Cases

### PASS top_10_revenue_vi
- Question: Hiện top 10 nhân viên có doanh thu cao nhất
- Provider: `planner`, RAG: `False`
- Rows: `5`, Chart: `True`
- SQL error: `None`
- Columns: `Mã nhân viên bán hàng, Tên nhân viên bán hàng, Tổng giá trị`

### PASS top_3_revenue_vi
- Question: Top 3 sales representative có tổng doanh thu cao nhất là ai?
- Provider: `planner`, RAG: `False`
- Rows: `3`, Chart: `True`
- SQL error: `None`
- Columns: `Mã nhân viên bán hàng, Tên nhân viên bán hàng, Tổng giá trị`

### PASS top_5_revenue_en
- Question: Show the top 5 sales reps by revenue
- Provider: `planner`, RAG: `False`
- Rows: `5`, Chart: `True`
- SQL error: `None`
- Columns: `Sales Rep ID, Sales Rep Name, Total Value`

### PASS above_average_vi
- Question: Có những nhân viên bán hàng nào có hiệu suất kinh doanh vượt trội so với bình quân?
- Provider: `planner`, RAG: `False`
- Rows: `2`, Chart: `True`
- SQL error: `None`
- Columns: `Mã nhân viên bán hàng, Tên nhân viên bán hàng, Tổng giá trị, Trung bình giá trị, Số dòng, Bình quân tổng giá trị, Chênh lệch so với bình quân, Tỷ lệ vượt bình quân (%)`

### PASS above_average_en
- Question: Which sales representatives are above the average sales performance?
- Provider: `planner`, RAG: `False`
- Rows: `2`, Chart: `True`
- SQL error: `None`
- Columns: `Sales Rep ID, Sales Rep Name, Total Value, Average Value, Record Count, Average Total Value, Difference Above Average, Above Average Rate (%)`

### PASS classify_performance_vi
- Question: Làm thế nào để phân loại Sales_Rep_ID thành các nhóm dựa trên hiệu suất bán hàng?
- Provider: `planner`, RAG: `False`
- Rows: `5`, Chart: `False`
- SQL error: `None`
- Columns: `Mã nhân viên bán hàng, Tên nhân viên bán hàng, Tổng giá trị, Trung bình giá trị, Số dòng, Nhóm hiệu suất`

### PASS classify_performance_en
- Question: Classify sales reps into performance groups based on sales value
- Provider: `planner`, RAG: `False`
- Rows: `5`, Chart: `False`
- SQL error: `None`
- Columns: `Sales Rep ID, Sales Rep Name, Total Value, Average Value, Record Count, Performance Group`

### PASS total_by_year_vi
- Question: Tổng doanh thu theo từng năm là bao nhiêu?
- Provider: `planner`, RAG: `False`
- Rows: `3`, Chart: `True`
- SQL error: `None`
- Columns: `Năm, Tổng giá trị`

### CHECK average_by_year_vi
- Question: Trung bình giá trị bán hàng theo năm là bao nhiêu?
- Provider: `planner`, RAG: `False`
- Rows: `3`, Chart: `True`
- SQL error: `None`
- Columns: `Năm, Tổng giá trị`

### CHECK average_by_rep_vi
- Question: Giá trị bán hàng trung bình của từng nhân viên là bao nhiêu?
- Provider: `planner`, RAG: `False`
- Rows: `5`, Chart: `True`
- SQL error: `None`
- Columns: `Tên nhân viên bán hàng, Tổng giá trị`

### PASS total_by_rep_vi
- Question: Tổng doanh thu của từng nhân viên bán hàng
- Provider: `planner`, RAG: `False`
- Rows: `5`, Chart: `True`
- SQL error: `None`
- Columns: `Tên nhân viên bán hàng, Tổng giá trị`

### PASS highest_postcode_vi
- Question: Postcode nào có tổng doanh thu cao nhất?
- Provider: `planner`, RAG: `False`
- Rows: `1`, Chart: `False`
- SQL error: `None`
- Columns: `Mã bưu chính, Tổng giá trị`

### PASS total_by_postcode_vi
- Question: Tổng doanh thu theo postcode
- Provider: `planner`, RAG: `False`
- Rows: `5`, Chart: `True`
- SQL error: `None`
- Columns: `Mã bưu chính, Tổng giá trị`

### PASS count_rows_vi
- Question: Đếm tổng số dòng trong Sheet1
- Provider: `planner`, RAG: `False`
- Rows: `1`, Chart: `False`
- SQL error: `None`
- Columns: `Số dòng`

### PASS show_all_vi
- Question: Hiển thị toàn bộ dữ liệu từ Sheet1
- Provider: `deepseek`, RAG: `True`
- Rows: `12`, Chart: `False`
- SQL error: `None`
- Columns: `Mã bưu chính, Mã nhân viên bán hàng, Tên nhân viên bán hàng, Năm, Giá trị`

### PASS sales_by_year_rep_vi
- Question: Doanh thu thay đổi theo từng năm cho mỗi nhân viên bán hàng như thế nào?
- Provider: `planner`, RAG: `False`
- Rows: `12`, Chart: `True`
- SQL error: `None`
- Columns: `Tên nhân viên bán hàng, Năm, Tổng giá trị`

### CHECK best_rep_each_year_vi
- Question: Nhân viên bán hàng nào có doanh thu cao nhất trong từng năm?
- Provider: `planner`, RAG: `False`
- Rows: `1`, Chart: `False`
- SQL error: `None`
- Columns: `Mã nhân viên bán hàng, Tên nhân viên bán hàng, Tổng giá trị`

### PASS low_revenue_reps_vi
- Question: Những nhân viên nào có doanh thu thấp nhất?
- Provider: `planner`, RAG: `False`
- Rows: `1`, Chart: `False`
- SQL error: `None`
- Columns: `Mã nhân viên bán hàng, Tên nhân viên bán hàng, Tổng giá trị`

### PASS min_revenue_vi
- Question: Doanh thu thấp nhất là bao nhiêu?
- Provider: `planner`, RAG: `False`
- Rows: `1`, Chart: `False`
- SQL error: `None`
- Columns: `Nhỏ nhất giá trị`

### PASS max_revenue_vi
- Question: Doanh thu cao nhất là bao nhiêu?
- Provider: `planner`, RAG: `False`
- Rows: `1`, Chart: `False`
- SQL error: `None`
- Columns: `Lớn nhất giá trị`

### PASS total_sales_scalar_vi
- Question: Tổng doanh thu toàn bộ là bao nhiêu?
- Provider: `planner`, RAG: `False`
- Rows: `1`, Chart: `False`
- SQL error: `None`
- Columns: `Tổng giá trị`

### PASS avg_sales_scalar_vi
- Question: Trung bình doanh thu toàn bộ là bao nhiêu?
- Provider: `planner`, RAG: `False`
- Rows: `1`, Chart: `False`
- SQL error: `None`
- Columns: `Trung bình giá trị`

### CHECK sales_distribution_postcode_vi
- Question: Phân bố doanh thu theo khu vực postcode như thế nào?
- Provider: `planner`, RAG: `False`
- Rows: `5`, Chart: `False`
- SQL error: `None`
- Columns: `Mã bưu chính, Số lượng`

### PASS rep_transaction_count_vi
- Question: Mỗi nhân viên bán hàng có bao nhiêu giao dịch?
- Provider: `planner`, RAG: `False`
- Rows: `5`, Chart: `True`
- SQL error: `None`
- Columns: `Tên nhân viên bán hàng, Số giao dịch`

### PASS distinct_rep_count_vi
- Question: Có bao nhiêu nhân viên bán hàng khác nhau?
- Provider: `planner`, RAG: `False`
- Rows: `1`, Chart: `False`
- SQL error: `None`
- Columns: `Số lượng nhân viên`

### PASS year_2012_sales_vi
- Question: Tổng doanh thu trong năm 2012 là bao nhiêu?
- Provider: `planner`, RAG: `False`
- Rows: `1`, Chart: `False`
- SQL error: `None`
- Columns: `Tổng giá trị`

### PASS postcodes_for_jane_vi
- Question: Jane bán hàng ở những postcode nào?
- Provider: `planner`, RAG: `False`
- Rows: `1`, Chart: `False`
- SQL error: `None`
- Columns: `Mã bưu chính`

### PASS sales_rep_name_alias_vi
- Question: Liệt kê tên nhân viên bán hàng và tổng doanh thu
- Provider: `planner`, RAG: `False`
- Rows: `5`, Chart: `True`
- SQL error: `None`
- Columns: `Tên nhân viên bán hàng, Tổng giá trị`

### PASS english_alias_en
- Question: List sales rep names and total revenue
- Provider: `planner`, RAG: `False`
- Rows: `5`, Chart: `True`
- SQL error: `None`
- Columns: `Sales Rep Name, Total Value`

### PASS no_chart_text_only_vi
- Question: Liệt kê tên nhân viên bán hàng
- Provider: `planner`, RAG: `False`
- Rows: `5`, Chart: `False`
- SQL error: `None`
- Columns: `Tên nhân viên bán hàng`

### PASS no_chart_id_only_vi
- Question: Liệt kê mã nhân viên bán hàng
- Provider: `planner`, RAG: `False`
- Rows: `5`, Chart: `False`
- SQL error: `None`
- Columns: `Mã nhân viên bán hàng`

### CHECK year_column_not_y_axis_vi
- Question: Hiển thị năm và trung bình giá trị bán hàng theo năm
- Provider: `planner`, RAG: `False`
- Rows: `3`, Chart: `True`
- SQL error: `None`
- Columns: `Năm, Tổng giá trị`

### PASS top_postcodes_en
- Question: Show top 5 postcodes by total sales value
- Provider: `planner`, RAG: `False`
- Rows: `5`, Chart: `True`
- SQL error: `None`
- Columns: `Postcode, Total Value`

### CHECK average_sales_rep_en
- Question: What is the average sales value for each sales representative?
- Provider: `planner`, RAG: `False`
- Rows: `5`, Chart: `True`
- SQL error: `None`
- Columns: `Sales Rep Name, Total Value`

### PASS total_by_year_en
- Question: What is the total sales value for each year?
- Provider: `planner`, RAG: `False`
- Rows: `3`, Chart: `True`
- SQL error: `None`
- Columns: `Year, Total Value`

### PASS count_by_year_en
- Question: How many sales records are there in each year?
- Provider: `planner`, RAG: `False`
- Rows: `3`, Chart: `False`
- SQL error: `None`
- Columns: `Year, Record Count`

### PASS sales_gap_top_bottom_vi
- Question: Chênh lệch doanh thu giữa nhân viên cao nhất và thấp nhất là bao nhiêu?
- Provider: `planner`, RAG: `False`
- Rows: `1`, Chart: `False`
- SQL error: `None`
- Columns: `Chênh lệch`

### PASS rep_sales_in_year_vi
- Question: Doanh thu của từng nhân viên trong năm 2013
- Provider: `planner`, RAG: `False`
- Rows: `4`, Chart: `True`
- SQL error: `None`
- Columns: `Tên nhân viên bán hàng, Tổng giá trị`

### CHECK postcode_average_vi
- Question: Trung bình doanh thu theo từng postcode
- Provider: `planner`, RAG: `False`
- Rows: `5`, Chart: `True`
- SQL error: `None`
- Columns: `Mã bưu chính, Tổng giá trị`

### CHECK rep_best_year_vi
- Question: Năm nào mỗi nhân viên bán hàng đạt doanh thu cao nhất?
- Provider: `planner`, RAG: `False`
- Rows: `1`, Chart: `False`
- SQL error: `None`
- Columns: `Mã nhân viên bán hàng, Tên nhân viên bán hàng, Tổng giá trị`
