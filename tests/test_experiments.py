"""正式实验费用口径手算测试。"""
import pytest
import copy
from experiments import run_experiments as experiment
from experiments.run_experiments import cost_breakdown, canonical_hash, validate_runs, validate_search


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


def business_result():
    return {'routes': {'routes': [{'sequence': ['WH01', 'S01', 'WH01'], 'cost': 12}],
                       'solve_time_sec': 1, 'peak_memory_mb': 20},
            'packing': {'vehicles': [{'placed': [{'x': 1, 'y': 2, 'z': 3}]}]},
            'loading_plan': {'plans': [{'steps': [1, 2]}]}}


def test_canonical_hash_performance_and_order():
    original = business_result()
    changed = copy.deepcopy(original)
    changed['routes']['solve_time_sec'] = 900
    changed['routes']['peak_memory_mb'] = 999
    changed['routes']['elapsed_sec'] = 80
    assert canonical_hash(original) == canonical_hash(changed)
    assert canonical_hash(dict(reversed(list(original.items())))) == canonical_hash(original)


@pytest.mark.parametrize('section', ['routes', 'packing', 'loading_plan'])
def test_canonical_hash_covers_complete_business(section):
    original = business_result()
    changed = copy.deepcopy(original)
    changed[section]['extra_business_field'] = 1
    assert canonical_hash(original) != canonical_hash(changed)


def raw_fixture():
    raw = {'rounds': 2, 'repeat': 3, 'source_sha256': {'run.py': 'source'},
           'datasets': [{'seed': seed, 'sha256': {'orders.json': str(seed)}} for seed in (42, 7)],
           'runs': []}
    for dataset in raw['datasets']:
        for mode in ('optimized', 'baseline'):
            for round_number in (1, 2):
                for repeat in (1, 2, 3):
                    record = business_result()
                    record.update({'seed': dataset['seed'], 'mode': mode, 'round': round_number,
                                   'repeat': repeat, 'source_sha256': raw['source_sha256'],
                                   'input_sha256': dataset['sha256'],
                                   'metrics': {'vehicle_count': 1, 'unassigned_order_count': 0},
                                   'search_diagnostics': {'stop_reason': 'local_search_completed',
                                                         'branches': 20000, 'solutions': 1,
                                                         'elapsed_sec': 1, 'safety_seconds': 120}})
                    record['business_hash'] = canonical_hash(record)
                    raw['runs'].append(record)
    return raw


def test_two_rounds_verify_all_24():
    result = validate_runs(raw_fixture())
    assert result['passed']
    assert result['verified_runs'] == 24
    assert [group['runs'] for group in result['groups']] == [6] * 4


@pytest.mark.parametrize('field', ['source_sha256', 'input_sha256', 'business_hash'])
def test_cross_run_freeze_rejects_change(field):
    raw = raw_fixture()
    raw['runs'][-1][field] = 'changed'
    with pytest.raises(ValueError):
        validate_runs(raw)


def test_business_variation_and_incomplete_round_rejected():
    raw = raw_fixture()
    raw['runs'][-1]['packing']['vehicles'][0]['placed'][0]['x'] += 1
    raw['runs'][-1]['business_hash'] = canonical_hash(raw['runs'][-1])
    with pytest.raises(ValueError, match='业务哈希不一致'):
        validate_runs(raw)
    raw['runs'].pop()
    with pytest.raises(ValueError, match='不完整'):
        validate_runs(raw)


def test_source_input_freeze(monkeypatch):
    monkeypatch.setattr(experiment, 'source_hashes', lambda: {'source': 'frozen'})
    monkeypatch.setattr(experiment, 'dataset_info', lambda _: ([], {'sha256': {'input': 'frozen'}}))
    experiment.assert_frozen({'source': 'frozen'}, {'input': 'frozen'}, None)
    with pytest.raises(ValueError, match='源码'):
        experiment.assert_frozen({'source': 'changed'}, {'input': 'frozen'}, None)
    with pytest.raises(ValueError, match='输入'):
        experiment.assert_frozen({'source': 'frozen'}, {'input': 'changed'}, None)


@pytest.mark.parametrize('reason', ['wall', 'unknown', 'not_started'])
def test_search_rejects_wall_or_unknown(reason):
    diagnostics = raw_fixture()['runs'][0]['search_diagnostics']
    diagnostics['stop_reason'] = reason
    with pytest.raises(ValueError, match='终止原因'):
        validate_search(diagnostics)


def test_search_requires_natural_finish():
    diagnostics = raw_fixture()['runs'][0]['search_diagnostics']
    validate_search(diagnostics)
    diagnostics['elapsed_sec'] = 120
    with pytest.raises(ValueError, match='墙钟'):
        validate_search(diagnostics)


@pytest.mark.parametrize('field,value', [('vehicle_count', 0), ('unassigned_order_count', 1)])
def test_optimized_business_validity(field, value):
    raw = raw_fixture()
    raw['runs'][0]['metrics'][field] = value
    with pytest.raises(ValueError, match='业务有效性'):
        validate_runs(raw)


def test_report_last_round_means_and_all_round_appendix(tmp_path):
    raw = raw_fixture()
    raw.update({'created_at': '测试时间', 'environment': {'python': 'test', 'platform': 'test', 'ortools': 'test'}})
    for dataset in raw['datasets']:
        dataset.update({'orders': 1, 'sku_rows': 1, 'total_items': 1, 'fragile_items': 0,
                        'fragile_ratio': 0, 'nonstackable_items': 0, 'nonstackable_ratio': 0})
    for record in raw['runs']:
        record['cost_breakdown'] = {}
        record['metrics'] = {key: 999 if record['round'] == 1 else 2 for _, key in experiment.FIELDS}
        record['metrics']['unassigned_order_count'] = 0
    raw['verification'] = validate_runs(raw)
    path = tmp_path / 'report.md'
    experiment.write_report(raw, path)
    text = path.read_text(encoding='utf-8')
    assert '完成 24 次' in text
    assert '末轮 12 次' in text
    assert '2.0000 ± 0.0000 [2.0000, 2.0000]' in text
    assert '999.0000 ±' not in text
    assert 'round1-optimized-1' in text and 'round2-optimized-1' in text
    assert '复核 24 次；结论：True' in text
