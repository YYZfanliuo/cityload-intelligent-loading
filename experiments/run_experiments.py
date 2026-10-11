"""T31：独立进程、统一全链求解计时与原生峰值内存的正式对比实验。"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import statistics
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def cost_breakdown(routes, orders, vehicles, network, rules):
    """以原矩阵和秒级最早服务时间核验运输费及含弃单罚费的目标值。"""
    index = {node: i for i, node in enumerate(network['node_order'])}
    fleet = {v['vehicle_id']: v for v in vehicles['vehicles']}
    by_store = {o['store_id']: o for o in orders['orders']}
    config = rules.get('routing', {})
    fixed = mileage = overtime = distance = 0.0
    details = []
    on_time = 0
    for route in routes['routes']:
        vehicle = fleet[route['vehicle_type']]
        km = math.fsum(network['distance_matrix_km'][index[a]][index[b]] for a, b in zip(route['sequence'], route['sequence'][1:]))
        base = float(vehicle.get('fixed_cost', 0))
        mile = km * float(vehicle.get('cost_per_km', 0))
        clock = 8 * 3600
        late_seconds = 0
        starts = {}
        for previous, current in zip(route['sequence'], route['sequence'][1:]):
            clock += math.ceil(network['duration_matrix_min'][index[previous]][index[current]] * 60 - 1e-9)
            if current == 'WH01':
                continue
            order = by_store[current]
            def seconds(stamp):
                """解析时间窗的精确秒数。"""
                parts = list(map(int, stamp.split(':')))
                return parts[0] * 3600 + parts[1] * 60 + (parts[2] if len(parts) == 3 else 0)
            earliest, latest = map(seconds, order['time_window'])
            if config.get('respect_time_window', True):
                clock = max(clock, earliest)
            starts[current] = clock
            on_time += clock <= latest
            if config.get('respect_time_window', True) and config.get('allow_overtime_penalty', False):
                late_seconds += max(0, clock - latest)
            clock += math.ceil(order.get('service_minutes', 0) * 60 - 1e-9)
        soft = late_seconds / 60 * config.get('overtime_penalty_per_min', 0)
        inferred = route['cost'] - base - mile
        if inferred < -0.001 or abs(inferred - soft) > 0.001:
            raise ValueError(f"{route['route_id']} 秒级费用不一致: {inferred} / {soft}")
        for stop in route.get('stop_details', []):
            start = starts[stop['node']]
            if stop['arrive'] != f'{start // 3600:02d}:{start // 60 % 60:02d}':
                raise ValueError('服务时间与最早到达重建不一致')
        fixed += base
        mileage += mile
        overtime += soft
        distance += km
        details.append({'route_id': route['route_id'], 'distance_km_exact': km, 'fixed_cost': base,
                        'mileage_cost': mile, 'overtime_seconds': late_seconds, 'overtime_cost': soft,
                        'inferred_overtime_cost': inferred, 'service_start_seconds': starts})
    drop = len(routes.get('unassigned_orders', [])) * config.get('unassigned_penalty', 1000)
    transport = fixed + mileage + overtime
    objective = transport + drop
    residual = routes['summary']['total_cost'] - objective
    if abs(residual) > 0.005:
        raise ValueError(f'目标费用不一致: {residual}')
    return {'fixed_cost': fixed, 'mileage_cost': mileage, 'overtime_cost': overtime,
            'transport_cost': transport, 'drop_penalty': drop, 'objective_cost': objective,
            'distance_km_exact': distance, 'summary_residual': residual,
            'exact_on_time_rate': on_time / len(orders['orders']) if orders['orders'] else 0,
            'per_route': details}


def dataset_info(data_dir):
    """统计按件加权的易碎、不可堆叠比例并冻结输入摘要。"""
    files = ('orders.json', 'vehicles.json', 'network.json', 'rules.json')
    inputs = [json.loads((data_dir / name).read_text(encoding='utf-8')) for name in files]
    items = [item for order in inputs[0]['orders'] for item in order['items']]
    total = sum(i['qty'] for i in items)
    fragile = sum(i['qty'] for i in items if i.get('fragile', False))
    nonstack = sum(i['qty'] for i in items if not i.get('stackable', True))
    return inputs, {'seed': inputs[0]['meta']['seed'], 'orders': len(inputs[0]['orders']),
                    'sku_rows': len(items), 'total_items': total, 'fragile_items': fragile,
                    'fragile_ratio': fragile / total, 'nonstackable_items': nonstack,
                    'nonstackable_ratio': nonstack / total,
                    'sha256': {name: hashlib.sha256((data_dir / name).read_bytes()).hexdigest() for name in files}}


def worker(data_dir, mode):
    """只执行求解和指标核验，不调用图件与报告渲染。"""
    from run import _solve, _evaluate
    from solver.routing import _peak_memory_mb
    inputs, info = dataset_info(data_dir)
    started = time.perf_counter()
    result = _solve(*inputs, baseline=mode == 'baseline')
    elapsed = time.perf_counter() - started
    peak = _peak_memory_mb()
    metrics = _evaluate(result, *inputs[:3])['metrics']
    costs = cost_breakdown(result['routes'], *inputs)
    metrics.update({'end_to_end_solve_sec': elapsed, 'native_peak_memory_mb': peak,
                    'original_solve_time_sec': result['routes']['solve_time_sec']})
    return {'seed': info['seed'], 'mode': mode, 'metrics': metrics, 'cost_breakdown': costs,
            'routes': result['routes'], 'packing_counts': [
                {'vehicle_instance': v['vehicle_instance'], 'placed': len(v['placed']),
                 'unplaced': sum(i.get('qty', 1) for i in v['unplaced'])} for v in result['packing']['vehicles']],
            'loading_plan_count': len(result['loading_plan']['plans'])}


FIELDS = [('车辆数', 'vehicle_count'), ('里程 km', 'distance_km_exact'),
          ('固定费 元', 'fixed_cost'), ('里程费 元', 'mileage_cost'), ('迟到费 元', 'overtime_cost'),
          ('运输费 元', 'transport_cost'), ('弃单罚 元', 'drop_penalty'), ('总目标 元', 'objective_cost'),
          ('平均载重率', 'avg_weight_load_rate'), ('平均体积率', 'avg_volume_utilization'),
          ('准时率（接口）', 'on_time_rate'), ('准时率（秒级）', 'exact_on_time_rate'),
          ('未派订单', 'unassigned_order_count'), ('总货件', 'total_item_count'),
          ('已装件', 'placed_item_count'), ('未装总件数', 'unplaced_item_count'),
          ('全链求解 s', 'end_to_end_solve_sec'), ('原始计时 s', 'original_solve_time_sec'),
          ('进程峰值 MiB', 'native_peak_memory_mb')]


def value(run, key):
    """读取统计指标或精确费用项。"""
    return run['cost_breakdown'][key] if key in run['cost_breakdown'] else run['metrics'][key]


def write_report(raw, path):
    """输出完整重复实验统计、每次指标和非夸大的差异解释。"""
    lines = ['# T31 正式对比实验与 T32 性能讨论', '',
             f"生成时间：{raw['created_at']}；完成 {len(raw['runs'])} 次独立子进程实验。", '',
             '## 实验口径与复现', '',
             '- seed42 与 seed7 各 optimized/baseline 三次，串行运行；每次新进程，输入摘要见 raw_results.json。',
             '- 全链求解时间：外部 perf_counter 包围 run._solve，包含路线、装箱、装载排序；不含导入、读取、指标和渲染。',
             '- 原始 solve_time 单列：优化方案仅路线，基线包含全链，不能直接横向比较。',
             '- Windows 原生 PeakWorkingSetSize / 1024²，覆盖进程生命周期且含 OR-Tools 原生分配；两算法使用相同模块导入，非 tracemalloc 或增量内存。峰值在全链求解后读取。',
             '- 三次统计使用样本标准差（n−1）；最小/最大反映本机重复运行，不代表总体置信区间。',
             '- OR-Tools 限时 10 秒的 CPU 搜索受负载影响；固定数据 seed 不保证路线或输出逐字节相同，冻结原始证据供核验。本机可能有其他任务，本报告不作为无后台负载的专门性能基准。',
             '- 费用按每条路线原始距离矩阵重算；08:00 出发，行驶及服务向上取整到秒、窗口等待重建服务时间。重建与输出 HH:MM 核验一致。',
             '- route.cost−固定费−精确里程费反推迟到费，与秒级重建误差≤0.001 元；summary 与运输费+弃单罚误差≤0.005 元（route/summary 三位小数舍入）。',
             '- eval.total_cost 是运输费而非含弃单的目标值；运输费、弃单罚、总目标分别披露。准时率分母为全部订单，秒级准时率另列避免 HH:MM 截断掩盖秒级迟到。',
             '- 平均载重率按已派路线订单重量计算，体积率按实际装入件计算；未装总件数包含拒装与未派订单全部货件，不能把路线派出等同全货交付。', '',
             '```bash', 'python experiments/run_experiments.py --repeat 3',
             '"C:/Users/y1933/.workbuddy/binaries/python/envs/default/Scripts/python.exe" experiments/run_experiments.py --repeat 3', '```', '',
             f"环境：Python {raw['environment']['python']}；{raw['environment']['platform']}；OR-Tools {raw['environment']['ortools']}。", '',
             '## 数据集差异（按 qty 加权）', '',
             '|seed|订单|SKU行|总件|易碎件|易碎占比|不可堆叠件|不可堆叠占比|', '|---|---:|---:|---:|---:|---:|---:|---:|']
    for d in raw['datasets']:
        lines.append(f"|{d['seed']}|{d['orders']}|{d['sku_rows']}|{d['total_items']}|{d['fragile_items']}|{d['fragile_ratio']:.2%}|{d['nonstackable_items']}|{d['nonstackable_ratio']:.2%}|")
    for seed in (42, 7):
        lines += ['', f'## seed{seed} 均值、样本标准差和范围', '', '|指标|optimized 均值 ± std [min, max]|baseline 均值 ± std [min, max]|', '|---|---:|---:|']
        groups = {mode: [r for r in raw['runs'] if r['seed'] == seed and r['mode'] == mode] for mode in ('optimized', 'baseline')}
        for label, key in FIELDS:
            cells = []
            for mode in groups:
                vals = [value(r, key) for r in groups[mode]]
                cells.append(f'{statistics.fmean(vals):.4f} ± {statistics.stdev(vals) if len(vals)>1 else 0:.4f} [{min(vals):.4f}, {max(vals):.4f}]')
            lines.append(f'|{label}|{cells[0]}|{cells[1]}|')
        lines += ['', '### 相对基线变化（均值计算）', '', '|指标|变化|', '|---|---:|']
        for label, key in [('里程减少', 'distance_km_exact'), ('运输费减少（交付量不同，不能独立视为公平节省）', 'transport_cost'), ('含弃单总目标减少', 'objective_cost'), ('未装件减少', 'unplaced_item_count')]:
            base = statistics.fmean(value(r, key) for r in groups['baseline'])
            opt = statistics.fmean(value(r, key) for r in groups['optimized'])
            lines.append(f'|{label}|{(base-opt)/base:.2%}|' if base else f'|{label}|基线为0，不计算相对值|')
    lines += ['', '## 每次运行完整附表', '']
    for seed in (42, 7):
        group = [r for r in raw['runs'] if r['seed'] == seed]
        lines += [f'### seed{seed}', '', '|指标|' + '|'.join(f"{r['mode']}-{r['repeat']}" for r in group) + '|', '|---|' + '---:|' * len(group)]
        for label, key in FIELDS:
            lines.append(f'|{label}|' + '|'.join(f'{value(r,key):.6f}' for r in group) + '|')
    a, b = raw['datasets']
    lines += ['', '## 归因边界与性能扩展（T32）', '',
              f"seed7 相对 seed42 易碎件增加 {b['fragile_items']-a['fragile_items']} 件，易碎占比增加 {(b['fragile_ratio']-a['fragile_ratio'])*100:.2f} 个百分点；不可堆叠占比变化 {(b['nonstackable_ratio']-a['nonstackable_ratio'])*100:.2f} 个百分点。易碎件不能承压、不可堆叠件不能承托，可能增加地板/顶层需求，解释装载难度，但两组同时改变距离、窗口、SKU和路线，不能据此做独立因果或声称单一因素贡献。seed7 未针对结果再次调参。",
              '本实验没有证明全部货件可交付；须同时查看未派订单与未装件，目标函数中的弃单罚也没有覆盖拒装件的业务代价。不能以运输费低或订单路由完成推导完整履约。',
              'CVRPTW 与三维装箱均属 NP-hard 问题。当前仅实测30店；未实测100店，不给出100店运行时或内存承诺。候选扩展：按地理与时间窗分区（须避免拆散跨区可行组合）、构造/缓存初始解预热、独立子问题并行及合并后全局约束检查；应以多规模实例实测质量/时间/内存和拒装率，再决定取舍。',
              '原始证据：experiments/raw_results.json，包含输入SHA256、环境、每次路线与时间、费用逐路线核验、件数与指标。正式运行完成后冻结，不将过去实验混入此次统计。', '']
    path.write_text('\n'.join(lines), encoding='utf-8')


def main():
    """串行启动独立子进程并写入证据与报告。"""
    parser = argparse.ArgumentParser(description='正式重复对比实验')
    parser.add_argument('--repeat', type=int, default=3)
    parser.add_argument('--worker', action='store_true')
    parser.add_argument('--data', type=Path)
    parser.add_argument('--mode', choices=('optimized', 'baseline'))
    args = parser.parse_args()
    if args.worker:
        print(json.dumps(worker(args.data, args.mode), ensure_ascii=True))
        return
    if args.repeat < 1:
        parser.error('--repeat 必须至少为1')
    import ortools
    directories = [ROOT / 'data', ROOT / 'data' / 'seed7']
    raw = {'created_at': datetime.now().isoformat(), 'environment': {'python': sys.version.split()[0],
           'platform': platform.platform(), 'ortools': ortools.__version__, 'executable': sys.executable},
           'datasets': [dataset_info(d)[1] for d in directories], 'runs': []}
    out = ROOT / 'experiments' / 'raw_results.json'
    for directory in directories:
        for mode in ('optimized', 'baseline'):
            for repetition in range(1, args.repeat + 1):
                completed = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--worker', '--data', str(directory), '--mode', mode], capture_output=True, text=True, encoding='utf-8')
                if completed.returncode:
                    raise RuntimeError(f'独立实验失败：{directory} {mode} 第{repetition}次\n{completed.stderr}')
                record = json.loads(completed.stdout)
                record['repeat'] = repetition
                raw['runs'].append(record)
                out.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding='utf-8')
                print(f"完成 seed{record['seed']} {mode} {repetition}/{args.repeat}，全链 {record['metrics']['end_to_end_solve_sec']:.3f}s", flush=True)
    write_report(raw, ROOT / 'experiments' / 'results.md')


if __name__ == '__main__':
    main()
