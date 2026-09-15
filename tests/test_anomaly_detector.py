import pandas as pd
from src.analytics.anomaly_detector import LogisticsAnomalyDetector


def test_z_score_calculation():
    detector = LogisticsAnomalyDetector(z_threshold=2.0)
    
    # Create sample data: 9 normal items ($0 variance) and 1 extreme outlier ($50 variance)
    data = {
        "tracking_id": [f"TRK_{i}" for i in range(10)],
        "package_id": [f"PKG_{i}" for i in range(10)],
        "invoice_number": ["INV_1"] * 10,
        "carrier_name": ["UPS"] * 10,
        "source_channel": ["UPS_EDI"] * 10,
        "scan_timestamp": [pd.Timestamp.utcnow()] * 10,
        "actual_scale_weight": [4.0] * 10,
        "billed_weight": [4.0] * 9 + [20.0],
        "weight_variance_lbs": [0.0] * 9 + [16.0],
        "expected_total_cost": [18.0] * 10,
        "billed_total": [18.0] * 9 + [68.0],
        "dollar_variance": [0.0] * 9 + [50.0],
        "anomaly_classification": ["CLEARED"] * 9 + ["WEIGHT_INFLATION"],
        "is_eligible_for_dispute": [False] * 9 + [True]
    }
    df = pd.DataFrame(data)

    scored_df = detector.compute_grouped_z_scores(df)

    # 9 normal items should have negative/low Z-scores
    assert scored_df.iloc[0]["is_statistical_outlier"] == False
    # The extreme outlier must exceed threshold 2.0
    assert scored_df.iloc[9]["is_statistical_outlier"] == True
    assert scored_df.iloc[9]["z_score"] > 2.0