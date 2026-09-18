import os
import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/logistics_db"
)


class LogisticsAnomalyDetector:
    """
    Statistical Outlier Detection Engine for Logistics Freight Audit.
    Loads dbt reconciliation marts, applies grouped Z-score thresholds,
    diagnoses channel-specific error patterns, and persists flagged anomalies.
    """

    def __init__(self, db_url: str = DATABASE_URL, z_threshold: float = 2.0):
        self.engine = create_engine(db_url)
        self.z_threshold = z_threshold

    def load_reconciliation_mart(self) -> pd.DataFrame:
        """Loads records from the dbt Gold Mart table."""
        query = """
        SELECT
            tracking_id,
            package_id,
            primary_invoice_number AS invoice_number,
            carrier_code AS carrier_name,
            primary_source_channel AS source_channel,
            scan_timestamp,
            actual_scale_weight_lbs AS actual_scale_weight,
            billed_weight_lbs AS billed_weight,
            weight_variance_lbs,
            expected_total_cost_usd AS expected_total_cost,
            billed_total_usd AS billed_total,
            total_variance_usd AS dollar_variance,
            anomaly_codes AS anomaly_classification,
            COALESCE(total_recoverable_usd, 0) > 0 AS is_eligible_for_dispute
        FROM analytics_marts.fct_reconciliation_marts;
        """
        df = pd.read_sql(query, con=self.engine)
        if df.empty:
            raise ValueError(
                "No records found in analytics_marts.fct_reconciliation_marts. Run 'dbt run' first."
            )
        return df

    def compute_grouped_z_scores(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Calculates baseline mean (mu) and standard deviation (sigma) of dollar variances
        grouped by carrier. Computes Z = (x - mu) / sigma and flags outliers where Z > 2.0.
        """
        df_scored = df.copy()

        # Convert numeric columns to float
        numeric_cols = [
            "dollar_variance",
            "weight_variance_lbs",
            "actual_scale_weight",
            "billed_weight",
            "expected_total_cost",
            "billed_total",
        ]
        for col in numeric_cols:
            df_scored[col] = df_scored[col].astype(float)

        def calculate_z(group):
            std = group.std(ddof=0)
            # Avoid division by zero if all values in group are identical
            if std == 0 or np.isnan(std):
                return pd.Series(0.0, index=group.index)
            return (group - group.mean()) / std

        # Compute Z-score grouped by carrier
        df_scored["z_score"] = (
            df_scored.groupby("carrier_name")["dollar_variance"]
            .transform(calculate_z)
            .round(2)
        )

        # Statistical outlier flag: Z-score strictly greater than threshold
        df_scored["is_statistical_outlier"] = df_scored["z_score"] > self.z_threshold

        return df_scored

    def analyze_source_channel_patterns(self, df: pd.DataFrame) -> dict:
        """
        Compares variance patterns across ingestion channels:
        - EDI feeds: High frequency, moderate variance -> Systematic tariff misconfiguration
        - Portal PDFs: Sporadic frequency, extreme variance -> Manual carrier adjustment / scale errors
        """
        summary = {}
        for channel_value, group in df.groupby("source_channel"):
            channel = str(channel_value)
            total_packages = len(group)
            outliers = group[group["is_statistical_outlier"]]
            outlier_count = len(outliers)
            avg_dollar_variance = group["dollar_variance"].mean()
            std_dollar_variance = group["dollar_variance"].std()
            max_overcharge = group["dollar_variance"].max()

            # Diagnostic classification heuristic
            if channel == "UPS_EDI":
                if outlier_count > 0 and avg_dollar_variance > 5.0:
                    diagnosis = "Systemic contract tariff / base rate misconfiguration in automated feed"
                else:
                    diagnosis = (
                        "Nominal automated EDI billing stream with standard variance"
                    )
            elif "PORTAL" in channel or "PDF" in channel:
                if max_overcharge > 25.0:
                    diagnosis = "Sporadic high-impact manual hub adjustment / optical scale calibration error"
                else:
                    diagnosis = "Low-variance regional portal adjustments"
            else:
                diagnosis = "Standard ingestion profile"

            summary[channel] = {
                "total_shipments": total_packages,
                "outlier_count": outlier_count,
                "outlier_rate_pct": (
                    round((outlier_count / total_packages) * 100, 2)
                    if total_packages > 0
                    else 0
                ),
                "avg_dollar_variance": round(avg_dollar_variance, 2),
                "std_dollar_variance": (
                    round(std_dollar_variance, 2)
                    if not np.isnan(std_dollar_variance)
                    else 0.0
                ),
                "max_overcharge": round(max_overcharge, 2),
                "diagnosis": diagnosis,
            }
        return summary

    def export_exceptions_to_db(self, df: pd.DataFrame, channel_analysis: dict) -> int:
        """
        Creates and persists high-confidence anomalies into source_enterprise.audit_flagged_exceptions.
        Combines statistical Z-score outliers with dbt rule-based dispute eligibility.
        """
        # A record is flagged if Z > 2.0 OR marked eligible by dbt verification gates
        flagged_df = df[
            (df["is_statistical_outlier"]) | (df["is_eligible_for_dispute"])
        ].copy()

        if flagged_df.empty:
            return 0

        # Attach diagnosis pattern based on source channel
        def get_diagnosis(row):
            channel = row["source_channel"]
            if channel in channel_analysis:
                return channel_analysis[channel]["diagnosis"]
            return "General billing discrepancy"

        flagged_df["pattern_diagnosis"] = flagged_df.apply(get_diagnosis, axis=1)
        flagged_df["dispute_status"] = "PENDING_REVIEW"
        flagged_df["flagged_at"] = pd.Timestamp.now("UTC")

        # DDL for the destination audit exception table
        create_table_ddl = """
        CREATE SCHEMA IF NOT EXISTS source_enterprise;
        CREATE TABLE IF NOT EXISTS source_enterprise.audit_flagged_exceptions (
            exception_id SERIAL PRIMARY KEY,
            tracking_id VARCHAR(64) NOT NULL,
            package_id VARCHAR(64),
            invoice_number VARCHAR(64),
            carrier_name VARCHAR(32),
            source_channel VARCHAR(32),
            actual_scale_weight NUMERIC(8, 2),
            billed_weight NUMERIC(8, 2),
            weight_variance_lbs NUMERIC(8, 2),
            expected_total_cost NUMERIC(10, 2),
            billed_total NUMERIC(10, 2),
            dollar_variance NUMERIC(10, 2),
            z_score NUMERIC(6, 2),
            anomaly_classification TEXT,
            pattern_diagnosis VARCHAR(256),
            dispute_status VARCHAR(32) DEFAULT 'PENDING_REVIEW',
            flagged_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        ALTER TABLE source_enterprise.audit_flagged_exceptions
            ALTER COLUMN anomaly_classification TYPE TEXT;
        TRUNCATE TABLE source_enterprise.audit_flagged_exceptions;
        """

        with self.engine.begin() as conn:
            conn.execute(text(create_table_ddl))

        # Select only the target columns for insert
        cols_to_export = [
            "tracking_id",
            "package_id",
            "invoice_number",
            "carrier_name",
            "source_channel",
            "actual_scale_weight",
            "billed_weight",
            "weight_variance_lbs",
            "expected_total_cost",
            "billed_total",
            "dollar_variance",
            "z_score",
            "anomaly_classification",
            "pattern_diagnosis",
            "dispute_status",
            "flagged_at",
        ]

        # The explicit constructor avoids pandas type-stub ambiguity for list selection.
        export_subset = pd.DataFrame(flagged_df, columns=cols_to_export)
        export_subset.to_sql(
            name="audit_flagged_exceptions",
            schema="source_enterprise",
            con=self.engine,
            if_exists="append",
            index=False,
        )

        return len(export_subset)

    def run(self):
        """Executes full statistical anomaly workflow."""
        print("=== 1. LOADING DBT RECONCILIATION MART ===")
        df_raw = self.load_reconciliation_mart()
        print(
            f"Loaded {len(df_raw)} records from analytics_marts.fct_reconciliation_marts."
        )

        print("\n=== 2. COMPUTING GROUPED STATISTICAL Z-SCORES ===")
        df_scored = self.compute_grouped_z_scores(df_raw)
        outliers = df_scored[df_scored["is_statistical_outlier"]]
        print(
            f"Calculated Z-scores across carriers. Identified {len(outliers)} statistical outliers (Z > {self.z_threshold})."
        )

        print("\n=== 3. INGESTION CHANNEL VARIANCE PATTERN ANALYSIS ===")
        patterns = self.analyze_source_channel_patterns(df_scored)
        for channel, metrics in patterns.items():
            print(f"\nChannel: [{channel}]")
            print(f"  - Total Shipments: {metrics['total_shipments']}")
            print(
                f"  - Outliers Flagged: {metrics['outlier_count']} ({metrics['outlier_rate_pct']}%)"
            )
            print(f"  - Mean Dollar Variance: ${metrics['avg_dollar_variance']}")
            print(f"  - Variance Std Dev: ${metrics['std_dollar_variance']}")
            print(f"  - Max Single Overcharge: ${metrics['max_overcharge']}")
            print(f"  - Operational Diagnosis: {metrics['diagnosis']}")

        print("\n=== 4. EXPORTING HIGH-CONFIDENCE ANOMALIES TO AUDIT TABLE ===")
        exported_count = self.export_exceptions_to_db(df_scored, patterns)
        print(
            f"Successfully persisted {exported_count} anomalies to source_enterprise.audit_flagged_exceptions."
        )

        return df_scored, patterns


if __name__ == "__main__":
    detector = LogisticsAnomalyDetector()
    detector.run()
