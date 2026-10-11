"""正式实验费用口径手算测试。"""
import pytest
from experiments.run_experiments import cost_breakdown


def fixture_cost():
    """构造08:00出发、61秒到店、迟到1秒的单路线。"""
    orders = {'orders': [{'order_id': 'O1', 'store_id': 'S01', 'time_window': ['08:00', '08:01'], 'service_minutes': 0},
                          {'order_id': 'O2', 'store_id': 'S02', 'time_window': ['08:00', '09:00'], 'service_minutes': 0}]}
    vehicles = {'vehicles': [{'vehicle_id': 'V1', 'fixed_cost': 10, 'cost_per_km': 3}]}
    network = {'node_order': ['WH01', 'S01'], 'distance_matrix_km': [[0, 1.23456], [1.23456, 0]],
               'duration_matrix_min': [[0, 1.001], [1.001, 0]]}
    rules = {'routing': {'respect_time_window': True, 'allow_overtime_penalty': True,
                          'overtime_penalty_per_min': 2, 'unassigned_penalty': 1000}}
    exact = 10 + 2 * 1.23456 * 3 + 2 / 60
    routes = {'routes': [{'route_id': 'R1', 'vehicle_type': 'V1', 'sequence': ['WH01', 'S01', 'WH01'],
                         'cost': round(exact, 3), 'stop_details': [{'node': 'S01', 'arrive': '08:01'}]}],
              'unassigned_orders': ['O2'], 'summary': {'total_cost': round(exact, 3) + 1000}}
    return routes, orders, vehicles, network, rules


def test_precise_mileage_seconds_and_drop():
    """精确矩阵里程、1秒罚费和弃单罚独立对账。"""
    result = cost_breakdown(*fixture_cost())
    assert result['fixed_cost'] == 10
    assert result['mileage_cost'] == pytest.approx(7.40736)
    assert result['overtime_cost'] == pytest.approx(1 / 30)
    assert result['drop_penalty'] == 1000
    assert result['objective_cost'] == pytest.approx(1017.4406933333)
    assert result['exact_on_time_rate'] == 0
    assert abs(result['summary_residual']) < .001


def test_reject_inconsistent_summary():
    """对账错误不能生成看似正确的报告。"""
    inputs = fixture_cost()
    inputs[0]['summary']['total_cost'] += .01
    with pytest.raises(ValueError, match='目标费用不一致'):
        cost_breakdown(*inputs)


def test_wait_and_no_lateness():
    """早到须等待，软惩罚不能由HH:MM截断估算。"""
    inputs = fixture_cost()
    inputs[1]['orders'][0]['time_window'] = ['09:00', '09:10']
    inputs[0]['routes'][0]['stop_details'][0]['arrive'] = '09:00'
    inputs[0]['routes'][0]['cost'] = round(17.40736, 3)
    inputs[0]['summary']['total_cost'] = 1017.407
    result = cost_breakdown(*inputs)
    assert result['overtime_cost'] == 0
    assert result['per_route'][0]['service_start_seconds']['S01'] == 32400
    assert result['exact_on_time_rate'] == .5
