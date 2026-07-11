"""
Machine-checkable promotion gates for the validation pipeline.

Strategy design and evidence of validity are SEPARATE concerns.
Nothing advances a stage without meeting predefined, objective criteria.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional

from utils.logger import get_logger
from config.settings import PROMOTION

logger = get_logger(__name__)


@dataclass
class GateResult:
    """Result of a promotion gate check."""
    stage: str
    passed: bool
    score: float
    threshold: float
    metric: str
    details: Dict
    
    def __str__(self):
        status = "PASS" if self.passed else "FAIL"
        return f"{self.stage}: {status} | {self.metric}={self.score:.3f} (threshold: {self.threshold})"


class PromotionCriteria:
    """
    Automated promotion gate checker.
    Produces computed pass/fail — not a manual judgment call.
    """
    
    def __init__(self):
        self.results: List[GateResult] = []
    
    def check_unit_tests(self, test_results: Dict) -> GateResult:
        """
        Stage: Unit Tests
        Criteria: 100% pass, no critical bugs
        """
        total = test_results.get("total", 0)
        passed = test_results.get("passed", 0)
        critical_bugs = test_results.get("critical_bugs", 0)
        
        pass_rate = passed / total if total > 0 else 0
        
        result = GateResult(
            stage="Unit Tests",
            passed=(pass_rate == 1.0 and critical_bugs == 0),
            score=pass_rate,
            threshold=1.0,
            metric="pass_rate",
            details={
                "total_tests": total,
                "passed": passed,
                "critical_bugs": critical_bugs,
            }
        )
        self.results.append(result)
        return result
    
    def check_backtest(self, backtest_results: Dict) -> GateResult:
        """
        Stage: Historical Backtest
        Criteria: Positive expectancy after fees and slippage
        """
        expectancy = backtest_results.get("expectancy", 0)
        total_trades = backtest_results.get("total_trades", 0)
        fees_included = backtest_results.get("fees_included", False)
        slippage_included = backtest_results.get("slippage_included", False)
        
        # Must have sufficient trades
        sufficient_trades = total_trades >= PROMOTION.min_trades_for_significance
        
        # Must be positive expectancy
        positive_expectancy = expectancy > PROMOTION.min_expectancy
        
        # Must include realistic costs
        realistic_costs = fees_included and slippage_included
        
        passed = sufficient_trades and positive_expectancy and realistic_costs
        
        result = GateResult(
            stage="Historical Backtest",
            passed=passed,
            score=expectancy,
            threshold=PROMOTION.min_expectancy,
            metric="expectancy",
            details={
                "total_trades": total_trades,
                "expectancy": expectancy,
                "fees_included": fees_included,
                "slippage_included": slippage_included,
                "sufficient_trades": sufficient_trades,
                "positive_expectancy": positive_expectancy,
                "realistic_costs": realistic_costs,
            }
        )
        self.results.append(result)
        return result
    
    def check_walkforward(self, walkforward_results: Dict) -> GateResult:
        """
        Stage: Walk-Forward Validation
        Criteria: PF > 1.3, Max DD < 15%, Sharpe > 1.0 — stable across windows
        """
        windows = walkforward_results.get("windows", [])
        
        if len(windows) < PROMOTION.walkforward_min_windows:
            return GateResult(
                stage="Walk-Forward",
                passed=False,
                score=0,
                threshold=PROMOTION.walkforward_min_windows,
                metric="window_count",
                details={"windows_found": len(windows), "required": PROMOTION.walkforward_min_windows}
            )
        
        # Check each window
        window_results = []
        all_pass = True
        
        for i, window in enumerate(windows):
            pf = window.get("profit_factor", 0)
            dd = window.get("max_drawdown", 1.0)
            sharpe = window.get("sharpe", 0)
            
            pf_pass = pf > PROMOTION.walkforward_min_profit_factor
            dd_pass = dd < PROMOTION.walkforward_max_dd
            sharpe_pass = sharpe > PROMOTION.walkforward_min_sharpe
            
            window_pass = pf_pass and dd_pass and sharpe_pass
            if not window_pass:
                all_pass = False
            
            window_results.append({
                "window": i + 1,
                "profit_factor": pf,
                "max_drawdown": dd,
                "sharpe": sharpe,
                "passed": window_pass,
            })
        
        # Average metrics across windows
        avg_pf = sum(w["profit_factor"] for w in windows) / len(windows)
        avg_dd = sum(w["max_drawdown"] for w in windows) / len(windows)
        avg_sharpe = sum(w["sharpe"] for w in windows) / len(windows)
        
        passed = all_pass and len(windows) >= PROMOTION.walkforward_min_windows
        
        result = GateResult(
            stage="Walk-Forward",
            passed=passed,
            score=avg_pf,
            threshold=PROMOTION.walkforward_min_profit_factor,
            metric="avg_profit_factor",
            details={
                "windows": len(windows),
                "avg_profit_factor": avg_pf,
                "avg_max_dd": avg_dd,
                "avg_sharpe": avg_sharpe,
                "window_results": window_results,
            }
        )
        self.results.append(result)
        return result
    
    def check_paper_trading(self, paper_results: Dict) -> GateResult:
        """
        Stage: Paper Trading
        Criteria: Minimum trades AND minimum duration, no execution issues
        """
        total_trades = paper_results.get("total_trades", 0)
        duration_days = paper_results.get("duration_days", 0)
        execution_issues = paper_results.get("execution_issues", 0)
        
        sufficient_trades = total_trades >= PROMOTION.paper_min_trades
        sufficient_duration = duration_days >= PROMOTION.paper_min_duration_days
        no_issues = execution_issues == 0
        
        passed = sufficient_trades and sufficient_duration and no_issues
        
        result = GateResult(
            stage="Paper Trading",
            passed=passed,
            score=total_trades,
            threshold=PROMOTION.paper_min_trades,
            metric="total_trades",
            details={
                "total_trades": total_trades,
                "duration_days": duration_days,
                "execution_issues": execution_issues,
                "sufficient_trades": sufficient_trades,
                "sufficient_duration": sufficient_duration,
                "no_issues": no_issues,
            }
        )
        self.results.append(result)
        return result
    
    def check_live_small(self, live_results: Dict) -> GateResult:
        """
        Stage: Live (Small Capital)
        Criteria: Matches expected behavior within acceptable variance
        """
        expected_return = live_results.get("expected_return", 0)
        actual_return = live_results.get("actual_return", 0)
        expected_trades = live_results.get("expected_trades", 0)
        actual_trades = live_results.get("actual_trades", 0)
        
        # Check variance
        if expected_return != 0:
            return_variance = abs(actual_return - expected_return) / abs(expected_return)
        else:
            return_variance = 0
        
        if expected_trades > 0:
            trade_variance = abs(actual_trades - expected_trades) / expected_trades
        else:
            trade_variance = 0
        
        within_tolerance = (
            return_variance <= PROMOTION.live_variance_tolerance and
            trade_variance <= PROMOTION.live_variance_tolerance
        )
        
        passed = within_tolerance
        
        result = GateResult(
            stage="Live (Small)",
            passed=passed,
            score=1 - return_variance,
            threshold=1 - PROMOTION.live_variance_tolerance,
            metric="behavior_match",
            details={
                "expected_return": expected_return,
                "actual_return": actual_return,
                "return_variance": return_variance,
                "expected_trades": expected_trades,
                "actual_trades": actual_trades,
                "trade_variance": trade_variance,
                "within_tolerance": within_tolerance,
            }
        )
        self.results.append(result)
        return result
    
    def evaluate_pipeline(self, results_by_stage: Dict[str, Dict]) -> Dict:
        """
        Run full pipeline evaluation.
        
        Args:
            results_by_stage: Dict with keys matching stage names,
                             values are the result dicts for each stage
        
        Returns:
            Complete evaluation with overall pass/fail
        """
        self.results = []
        
        stages = [
            ("unit_tests", self.check_unit_tests),
            ("backtest", self.check_backtest),
            ("walkforward", self.check_walkforward),
            ("paper", self.check_paper_trading),
            ("live_small", self.check_live_small),
        ]
        
        pipeline_passed = True
        first_failure = None
        
        for stage_name, check_func in stages:
            if stage_name in results_by_stage:
                result = check_func(results_by_stage[stage_name])
                
                if not result.passed and pipeline_passed:
                    pipeline_passed = False
                    first_failure = stage_name
                
                logger.info(str(result))
        
        # Determine highest passed stage
        passed_stages = [r.stage for r in self.results if r.passed]
        highest_stage = passed_stages[-1] if passed_stages else "None"
        
        return {
            "overall_pass": pipeline_passed,
            "highest_stage": highest_stage,
            "first_failure": first_failure,
            "stage_results": [r.__dict__ for r in self.results],
            "can_advance": pipeline_passed,
        }
    
    def generate_report(self) -> str:
        """Generate human-readable promotion report."""
        lines = ["=" * 60, "PROMOTION PIPELINE REPORT", "=" * 60]
        
        for result in self.results:
            status = "✅ PASS" if result.passed else "❌ FAIL"
            lines.append(f"\n{status} | {result.stage}")
            lines.append(f"  Metric: {result.metric} = {result.score:.4f}")
            lines.append(f"  Threshold: {result.threshold}")
            for key, val in result.details.items():
                if isinstance(val, (int, float)):
                    lines.append(f"  {key}: {val:.4f}")
                elif isinstance(val, bool):
                    lines.append(f"  {key}: {'Yes' if val else 'No'}")
        
        overall = all(r.passed for r in self.results) if self.results else False
        lines.append(f"\n{'=' * 60}")
        lines.append(f"OVERALL: {'✅ CAN ADVANCE' if overall else '❌ BLOCKED'}")
        lines.append(f"{'=' * 60}")
        
        return "\n".join(lines)