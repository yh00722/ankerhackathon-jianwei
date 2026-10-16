/**
 * @name 训练动作进行中
 * @description 老人端的大字卡片：口令、次数、保持秒数（进度条）、剩余时间，以及刚完成那一次的结果。
 * 开始前讲「怎样算一次」；计数时亮出现在该做的那一步和抬腿进度（RepGuide），每算上一次大数字跳一下。
 */

import type { ExerciseStatus } from '../api';
import { RepGuide } from './RepGuide';

const SIDE: Record<string, string> = { left: '左腿', right: '右腿' };

export function ExerciseCard({ ex, flash, warn, onCancel }: {
  ex: ExerciseStatus; flash: string; warn?: string; onCancel: () => void;
}) {
  const k = ex.exercise;
  const counting = ex.phase === 'running';
  let big: string | null = null;
  let sub = '';
  let hold: { now: number; target: number } | null = null;

  if (ex.phase === 'countdown') {
    big = String(Math.ceil(ex.remaining_s ?? 0));
  } else if (k === 'knee_ext' || k === 'knee_flex') {
    if (counting) {
      big = `${ex.reps ?? 0}`;
      sub = `目标 ${ex.target_reps} 次`;
      const sides = Object.entries(ex.by_side || {});
      if (sides.length > 1) sub += ` · ${sides.map(([s, n]) => `${SIDE[s]} ${n}`).join('、')}`;
      if (ex.holding_s != null) hold = { now: ex.holding_s, target: ex.hold_target_s ?? 3 };
    }
  } else if (k === 'balance') {
    if (counting) {
      big = `${(ex.holding_s ?? 0).toFixed(1)} 秒`;
      hold = { now: ex.holding_s ?? 0, target: ex.target_s ?? 10 };
    }
    sub = `${ex.variant_text || ''} · 第 ${Math.min((ex.attempts ?? 0) + (counting ? 1 : 0), ex.max_attempts ?? 3)} / ${ex.max_attempts} 次`;
    if (ex.best_s != null) sub += ` · 最长 ${ex.best_s.toFixed(1)} 秒`;
  } else if (k === 'march2min') {
    if (counting) {
      big = `${ex.reps ?? 0}`;
      sub = `右膝次数（左膝 ${ex.by_side?.left ?? 0}）· 剩余 ${Math.ceil(ex.remaining_s ?? 0)} 秒`;
    }
  } else if (k === 'weight_shift') {
    if (counting) {
      big = `${ex.reps ?? 0}`;
      sub = `目标 ${ex.target_reps} 次 · 左 ${ex.by_side?.left ?? 0}、右 ${ex.by_side?.right ?? 0} · 剩余 ${Math.ceil(ex.remaining_s ?? 0)} 秒`;
    }
  }

  const params = { hold_s: ex.hold_target_s, target_s: ex.target_s };
  const repBased = counting && k !== 'balance';
  return (
    <div className="sts ex">
      <div className="ex__name">{ex.name}</div>
      <div className="sts__prompt">{ex.prompt}</div>
      {big != null && (
        // 次数变了就重新挂载，播一次「跳一下」的动画
        <div className={`sts__big${repBased ? ' sts__big--pop' : ''}`} key={repBased ? `r${ex.reps ?? 0}` : 'big'}>{big}</div>
      )}
      {sub && <div className="sts__sub">{sub}</div>}
      {!counting && ['prep', 'countdown', 'between'].includes(ex.phase) && <RepGuide kind={k} params={params} mode="intro" />}
      {counting && <RepGuide kind={k} params={params} mode="live" stage={ex.stage} progress={ex.progress} />}
      {hold && (
        <div className="ex__hold">
          <div className="progress">
            <div className={`progress__bar${hold.now >= hold.target ? ' progress__bar--ok' : ''}`}
              style={{ width: `${Math.min(100, (100 * hold.now) / hold.target)}%` }} />
          </div>
          <div className="muted">保持 {hold.now.toFixed(1)} / {hold.target} 秒</div>
        </div>
      )}
      {warn && <div className="ex__warn">{warn}</div>}
      {flash && !warn && <div className="ex__flash">{flash}</div>}
      <button className="e-btn e-btn--ghost" onClick={onCancel}>
        {counting ? '结束这组' : '取消'}
      </button>
      {counting && <div className="e-note">提前结束，做过的也会保存。</div>}
    </div>
  );
}
