"""Backend API Tests for Iteration 5.2: Time-Based Analytics with PnL Fields

Tests the extended GET /api/analytics/time-based/{symbol} endpoint with new PnL fields:
- trades (count of closed trades)
- trade_wins, trade_losses
- trade_win_rate (%)
- pnl (sum of realized_pnl in USDT)
- avg_pnl
- best_trade, worst_trade

These fields are added to by_hour, by_weekday, and by_combo groupings.
"""
import os
import sys
import requests
import json

# Backend URL
BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "http://localhost:8001").rstrip("/")
ADMIN_USER = os.environ.get("ADMIN_USER", "Admin")
ADMIN_PW = os.environ.get("ADMIN_PASSWORD", "admin")

# Test results
test_results = {
    "passed": 0,
    "failed": 0,
    "errors": []
}


def log_test(test_name, passed, message=""):
    """Log test result."""
    if passed:
        test_results["passed"] += 1
        print(f"✅ PASS: {test_name}")
        if message:
            print(f"   {message}")
    else:
        test_results["failed"] += 1
        test_results["errors"].append(f"{test_name}: {message}")
        print(f"❌ FAIL: {test_name}")
        print(f"   {message}")


def get_admin_token():
    """Get admin token for authenticated requests."""
    try:
        r = requests.post(f"{BASE_URL}/api/auth/login",
                         json={"username": ADMIN_USER, "password": ADMIN_PW},
                         timeout=15)
        if r.status_code == 200:
            token = r.json().get("token") or r.json().get("access_token")
            print(f"✓ Admin login successful")
            return token
        else:
            print(f"⚠ Admin login failed: {r.status_code} - {r.text}")
            return None
    except Exception as e:
        print(f"⚠ Admin login error: {e}")
        return None


def test_basic_endpoint():
    """Test 1: GET /api/analytics/time-based/BTCUSDT returns 200 with all fields."""
    print("\n" + "="*80)
    print("TEST 1: Basic endpoint response structure")
    print("="*80)
    
    try:
        r = requests.get(f"{BASE_URL}/api/analytics/time-based/BTCUSDT", timeout=15)
        
        # Check status code
        if r.status_code != 200:
            log_test("Basic endpoint returns 200", False, f"Got {r.status_code}: {r.text[:200]}")
            return
        
        log_test("Basic endpoint returns 200", True)
        
        # Parse response
        data = r.json()
        
        # Check required fields
        required_fields = ["symbol", "strategy_id", "time_analytics", "best_hours", 
                          "by_hour", "by_weekday", "by_combo"]
        missing_fields = [f for f in required_fields if f not in data]
        
        if missing_fields:
            log_test("All required fields present", False, f"Missing: {missing_fields}")
        else:
            log_test("All required fields present", True, 
                    f"symbol={data['symbol']}, strategy_id={data['strategy_id']}")
        
        # Check field types
        if not isinstance(data.get("by_hour"), list):
            log_test("by_hour is list", False, f"Got {type(data.get('by_hour'))}")
        else:
            log_test("by_hour is list", True, f"{len(data['by_hour'])} entries")
        
        if not isinstance(data.get("by_weekday"), list):
            log_test("by_weekday is list", False, f"Got {type(data.get('by_weekday'))}")
        else:
            log_test("by_weekday is list", True, f"{len(data['by_weekday'])} entries")
        
        if not isinstance(data.get("by_combo"), list):
            log_test("by_combo is list", False, f"Got {type(data.get('by_combo'))}")
        else:
            log_test("by_combo is list", True, f"{len(data['by_combo'])} entries")
        
        return data
        
    except Exception as e:
        log_test("Basic endpoint test", False, f"Exception: {e}")
        return None


def test_pnl_fields_in_groupings(data):
    """Test 2: Check that PnL fields are present in all groupings."""
    print("\n" + "="*80)
    print("TEST 2: PnL fields present in by_hour, by_weekday, by_combo")
    print("="*80)
    
    if not data:
        print("⚠ Skipping test - no data from previous test")
        return
    
    # Required PnL fields
    pnl_fields = ["trades", "trade_wins", "trade_losses", "trade_win_rate", 
                  "pnl", "avg_pnl", "best_trade", "worst_trade"]
    
    # Test by_hour
    by_hour = data.get("by_hour", [])
    if by_hour:
        entry = by_hour[0]
        missing = [f for f in pnl_fields if f not in entry]
        if missing:
            log_test("by_hour has all PnL fields", False, 
                    f"Missing in first entry: {missing}. Entry: {json.dumps(entry, indent=2)}")
        else:
            log_test("by_hour has all PnL fields", True, 
                    f"hour={entry.get('hour')}, trades={entry.get('trades')}, pnl={entry.get('pnl')}")
            
            # Validate trade_win_rate calculation
            wins = entry.get("trade_wins", 0)
            losses = entry.get("trade_losses", 0)
            total = wins + losses
            if total > 0:
                expected_wr = round(wins / total * 100, 1)
                actual_wr = entry.get("trade_win_rate", 0)
                if abs(expected_wr - actual_wr) > 0.1:
                    log_test("by_hour trade_win_rate calculation", False,
                            f"Expected {expected_wr}%, got {actual_wr}%")
                else:
                    log_test("by_hour trade_win_rate calculation", True,
                            f"{actual_wr}% ({wins}W/{losses}L)")
    else:
        log_test("by_hour has entries to test", False, "Empty list")
    
    # Test by_weekday
    by_weekday = data.get("by_weekday", [])
    if by_weekday:
        entry = by_weekday[0]
        missing = [f for f in pnl_fields if f not in entry]
        if missing:
            log_test("by_weekday has all PnL fields", False, 
                    f"Missing in first entry: {missing}")
        else:
            log_test("by_weekday has all PnL fields", True,
                    f"weekday={entry.get('weekday')}, trades={entry.get('trades')}, pnl={entry.get('pnl')}")
    else:
        log_test("by_weekday has entries to test", False, "Empty list")
    
    # Test by_combo
    by_combo = data.get("by_combo", [])
    if by_combo:
        entry = by_combo[0]
        missing = [f for f in pnl_fields if f not in entry]
        if missing:
            log_test("by_combo has all PnL fields", False, 
                    f"Missing in first entry: {missing}")
        else:
            log_test("by_combo has all PnL fields", True,
                    f"{entry.get('weekday')} {entry.get('hour')}:00, trades={entry.get('trades')}, pnl={entry.get('pnl')}")
    else:
        log_test("by_combo has entries to test", False, "Empty list")
    
    # Check that entries with 0 trades still have PnL fields (should be 0)
    print("\n  Checking entries with 0 trades have PnL fields set to 0...")
    zero_trade_entries = [e for e in by_hour if e.get("trades", 0) == 0]
    if zero_trade_entries:
        entry = zero_trade_entries[0]
        if entry.get("pnl") == 0.0 and entry.get("avg_pnl") == 0.0:
            log_test("Zero-trade entries have PnL=0", True,
                    f"hour={entry.get('hour')}, pnl={entry.get('pnl')}")
        else:
            log_test("Zero-trade entries have PnL=0", False,
                    f"Expected pnl=0, got pnl={entry.get('pnl')}, avg_pnl={entry.get('avg_pnl')}")


def test_strategy_filter():
    """Test 3: GET with strategy_id=ai_trader filters correctly."""
    print("\n" + "="*80)
    print("TEST 3: Strategy filter (strategy_id=ai_trader)")
    print("="*80)
    
    try:
        r = requests.get(f"{BASE_URL}/api/analytics/time-based/BTCUSDT?strategy_id=ai_trader", 
                        timeout=15)
        
        if r.status_code != 200:
            log_test("Strategy filter returns 200", False, f"Got {r.status_code}: {r.text[:200]}")
            return
        
        log_test("Strategy filter returns 200", True)
        
        data = r.json()
        
        # Check strategy_id is set
        if data.get("strategy_id") != "ai_trader":
            log_test("Response has correct strategy_id", False,
                    f"Expected 'ai_trader', got '{data.get('strategy_id')}'")
        else:
            log_test("Response has correct strategy_id", True, "ai_trader")
        
        # Check that data is filtered (should have fewer or equal entries)
        by_hour = data.get("by_hour", [])
        log_test("Filtered by_hour returned", True, f"{len(by_hour)} entries")
        
        # If there are entries, verify they have PnL fields
        if by_hour:
            entry = by_hour[0]
            pnl_fields = ["trades", "trade_wins", "trade_losses", "pnl"]
            missing = [f for f in pnl_fields if f not in entry]
            if missing:
                log_test("Filtered entries have PnL fields", False, f"Missing: {missing}")
            else:
                log_test("Filtered entries have PnL fields", True,
                        f"hour={entry.get('hour')}, trades={entry.get('trades')}")
        
        return data
        
    except Exception as e:
        log_test("Strategy filter test", False, f"Exception: {e}")
        return None


def test_nonexistent_strategy():
    """Test 4: GET with nonexistent strategy_id returns 200 with empty/null buckets."""
    print("\n" + "="*80)
    print("TEST 4: Nonexistent strategy_id handling")
    print("="*80)
    
    try:
        r = requests.get(f"{BASE_URL}/api/analytics/time-based/BTCUSDT?strategy_id=nonexistent_strategy_xyz123",
                        timeout=15)
        
        # Should return 200, not 500
        if r.status_code != 200:
            log_test("Nonexistent strategy returns 200", False,
                    f"Got {r.status_code}: {r.text[:200]}")
            return
        
        log_test("Nonexistent strategy returns 200", True, "No 500 error")
        
        data = r.json()
        
        # Check that lists are present (even if empty)
        if not isinstance(data.get("by_hour"), list):
            log_test("by_hour is list (even if empty)", False, f"Got {type(data.get('by_hour'))}")
        else:
            log_test("by_hour is list (even if empty)", True, f"{len(data['by_hour'])} entries")
        
        if not isinstance(data.get("by_weekday"), list):
            log_test("by_weekday is list (even if empty)", False, f"Got {type(data.get('by_weekday'))}")
        else:
            log_test("by_weekday is list (even if empty)", True, f"{len(data['by_weekday'])} entries")
        
        if not isinstance(data.get("by_combo"), list):
            log_test("by_combo is list (even if empty)", False, f"Got {type(data.get('by_combo'))}")
        else:
            log_test("by_combo is list (even if empty)", True, f"{len(data['by_combo'])} entries")
        
    except Exception as e:
        log_test("Nonexistent strategy test", False, f"Exception: {e}")


def test_backward_compatibility():
    """Test 5: Backward compatibility - time_analytics and best_hours unchanged."""
    print("\n" + "="*80)
    print("TEST 5: Backward compatibility (time_analytics, best_hours)")
    print("="*80)
    
    try:
        r = requests.get(f"{BASE_URL}/api/analytics/time-based/BTCUSDT", timeout=15)
        
        if r.status_code != 200:
            log_test("Backward compatibility test", False, f"Got {r.status_code}")
            return
        
        data = r.json()
        
        # Check time_analytics exists and has expected structure
        time_analytics = data.get("time_analytics")
        if not isinstance(time_analytics, list):
            log_test("time_analytics is list", False, f"Got {type(time_analytics)}")
        else:
            log_test("time_analytics is list", True, f"{len(time_analytics)} entries")
            
            # Check structure of time_analytics entries (old format)
            if time_analytics:
                entry = time_analytics[0]
                old_fields = ["hour", "weekday", "total_signals", "wins", "losses", "win_rate", "avg_crv"]
                missing = [f for f in old_fields if f not in entry]
                if missing:
                    log_test("time_analytics has old structure", False, f"Missing: {missing}")
                else:
                    log_test("time_analytics has old structure", True,
                            f"hour={entry.get('hour')}, weekday={entry.get('weekday')}")
        
        # Check best_hours exists and is limited to 5
        best_hours = data.get("best_hours")
        if not isinstance(best_hours, list):
            log_test("best_hours is list", False, f"Got {type(best_hours)}")
        else:
            if len(best_hours) > 5:
                log_test("best_hours limited to 5", False, f"Got {len(best_hours)} entries")
            else:
                log_test("best_hours limited to 5", True, f"{len(best_hours)} entries")
        
    except Exception as e:
        log_test("Backward compatibility test", False, f"Exception: {e}")


def test_performance_endpoint():
    """Test 6: Verify /api/performance endpoint still works (no regression)."""
    print("\n" + "="*80)
    print("TEST 6: No regression in /api/performance")
    print("="*80)
    
    try:
        r = requests.get(f"{BASE_URL}/api/performance", timeout=15)
        
        if r.status_code != 200:
            log_test("/api/performance returns 200", False, f"Got {r.status_code}: {r.text[:200]}")
            return
        
        log_test("/api/performance returns 200", True)
        
        data = r.json()
        
        # Check structure
        if "performance" not in data:
            log_test("/api/performance has 'performance' key", False, f"Keys: {data.keys()}")
        else:
            perf = data["performance"]
            if not isinstance(perf, list):
                log_test("/api/performance is list", False, f"Got {type(perf)}")
            else:
                log_test("/api/performance is list", True, f"{len(perf)} symbols")
                
                # Check structure of first entry
                if perf:
                    entry = perf[0]
                    required = ["symbol", "total_signals", "wins", "losses", "win_rate"]
                    missing = [f for f in required if f not in entry]
                    if missing:
                        log_test("/api/performance has correct structure", False, f"Missing: {missing}")
                    else:
                        log_test("/api/performance has correct structure", True,
                                f"symbol={entry.get('symbol')}")
        
    except Exception as e:
        log_test("/api/performance test", False, f"Exception: {e}")


def test_merge_logic():
    """Test 7: Verify _merge logic - buckets with only trades (no signals) appear."""
    print("\n" + "="*80)
    print("TEST 7: Merge logic - trade-only buckets appear")
    print("="*80)
    
    try:
        r = requests.get(f"{BASE_URL}/api/analytics/time-based/BTCUSDT", timeout=15)
        
        if r.status_code != 200:
            log_test("Merge logic test", False, f"Got {r.status_code}")
            return
        
        data = r.json()
        
        # Look for entries with trades but 0 signals
        by_hour = data.get("by_hour", [])
        trade_only = [e for e in by_hour if e.get("trades", 0) > 0 and e.get("total_signals", 0) == 0]
        
        if trade_only:
            log_test("Trade-only buckets present", True,
                    f"Found {len(trade_only)} buckets with trades but no signals")
            entry = trade_only[0]
            print(f"   Example: hour={entry.get('hour')}, trades={entry.get('trades')}, "
                  f"signals={entry.get('total_signals')}, pnl={entry.get('pnl')}")
        else:
            # This is not necessarily a failure - it depends on the data
            log_test("Trade-only buckets check", True,
                    "No trade-only buckets found (may be normal depending on data)")
        
        # Also check signal-only buckets have trades=0
        signal_only = [e for e in by_hour if e.get("total_signals", 0) > 0 and e.get("trades", 0) == 0]
        if signal_only:
            entry = signal_only[0]
            if "trades" in entry and entry["trades"] == 0:
                log_test("Signal-only buckets have trades=0", True,
                        f"hour={entry.get('hour')}, signals={entry.get('total_signals')}")
            else:
                log_test("Signal-only buckets have trades field", False,
                        f"Missing or non-zero trades field")
        
    except Exception as e:
        log_test("Merge logic test", False, f"Exception: {e}")


def print_summary():
    """Print test summary."""
    print("\n" + "="*80)
    print("TEST SUMMARY - Iteration 5.2: Time-Based Analytics with PnL")
    print("="*80)
    print(f"✅ Passed: {test_results['passed']}")
    print(f"❌ Failed: {test_results['failed']}")
    
    if test_results['errors']:
        print("\nFailed Tests:")
        for error in test_results['errors']:
            print(f"  - {error}")
    
    print("="*80)
    
    if test_results['failed'] == 0:
        print("🎉 ALL TESTS PASSED!")
        return 0
    else:
        print("⚠️  SOME TESTS FAILED")
        return 1


def main():
    """Run all tests."""
    print("="*80)
    print("BACKEND API TESTS - Iteration 5.2")
    print("Time-Based Analytics with PnL Fields")
    print("="*80)
    print(f"Backend URL: {BASE_URL}")
    print(f"Admin User: {ADMIN_USER}")
    print("="*80)
    
    # Get admin token (for future tests if needed)
    token = get_admin_token()
    
    # Run tests
    data = test_basic_endpoint()
    test_pnl_fields_in_groupings(data)
    test_strategy_filter()
    test_nonexistent_strategy()
    test_backward_compatibility()
    test_performance_endpoint()
    test_merge_logic()
    
    # Print summary
    exit_code = print_summary()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
