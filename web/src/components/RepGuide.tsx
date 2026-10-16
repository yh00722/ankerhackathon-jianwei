/**
 * @name 怎样算一次
 * @description 训练时的计数提示。开始前讲清楚这个动作怎样算一次、什么情况不算；
 * 计数时把现在该做的那一步亮出来，并画一条进度条（抬到 / 站到那条线就开始算）。
 * 步骤和进度来自服务端的实时状态（stage / progress），这里只负责显示。
 */

type Step = [icon: string, text: string];

/** 不换行的空格：「保持 3 秒」在窄格子里不被拆成两行 */
const NB = '\u00a0';

interface Rule {
  steps: Step[];
  miss: string;
  /** 进度条的名字和那条线的名字（没有进度的动作不画） */
  gauge?: { label: string; line: string };
}

function rules(kind: string, p: Record<string, any>): Rule | null {
  const hold = p.hold_s ?? 3;
  switch (kind) {
    case 'sts30':
      return {
        steps: [['🙆', '双手抱胸'], ['⬆️', '站直'], ['⬇️', '坐下']],
        miss: '站直才算一次。手离开胸前也算，但会记下「用了手」。',
        gauge: { label: '起身', line: '站直线' },
      };
    case 'knee_ext':
      return {
        steps: [['🦵', '抬平小腿'], ['⏱️', `保持${NB}${hold}${NB}秒`], ['⬇️', '慢慢放下']],
        miss: '放下才算做完一次；腿出了画面，这次不算。',
        gauge: { label: '抬腿', line: '抬平线' },
      };
    case 'knee_flex':
      return {
        steps: [['🦵', '脚往后收'], ['⏱️', `保持${NB}${hold}${NB}秒`], ['↩️', '放回原处']],
        miss: '放回原处才算做完一次；脚出了画面，这次不算。',
        gauge: { label: '收腿', line: '到位线' },
      };
    case 'balance':
      return { steps: [['🧍', '站好不扶'], ['⏱️', `保持${NB}${p.target_s ?? 10}${NB}秒`], ['🙌', '扶了就停']], miss: '扶东西或挪脚，这一次计时就结束。' };
    case 'march2min':
      return { steps: [['🚶', '原地抬膝'], ['⬆️', '右膝抬高'], ['⬇️', '放下']], miss: '右膝抬起一次算一次，一共 2 分钟。' };
    case 'weight_shift':
      return { steps: [['🤲', '扶着台面'], ['↔️', '移到一侧'], ['⏺️', '回到中间']], miss: '移到一侧再回到中间，算一次。' };
    default:
      return null;
  }
}

/** 服务端的 stage → 现在亮第几步 */
const ACTIVE: Record<string, number> = { lift: 0, hold: 1, lower: 2, up: 1, down: 2 };

export function RepGuide({ kind, params = {}, mode, stage, progress, lineAt = 1, warnStep }: {
  kind: string;
  params?: Record<string, any>;
  /** intro：开始前（标题 + 步骤 + 不算的情况）；live：计数中（步骤高亮 + 进度条） */
  mode: 'intro' | 'live';
  stage?: string;
  progress?: number;
  /** 进度条上那条线的位置（伸膝、屈膝是 1；坐站是站直线 0.85） */
  lineAt?: number;
  /** 需要提醒的那一步（比如坐站时手离开了胸前，亮第一步） */
  warnStep?: number;
}) {
  const r = rules(kind, params);
  if (!r) return null;
  const active = mode === 'live' && stage ? ACTIVE[stage] : undefined;
  const max = Math.max(1.2, lineAt * 1.25);
  const pct = (v: number) => `${Math.min(100, (100 * v) / max)}%`;
  const reached = progress != null && progress >= lineAt;
  return (
    <div className={`rep-guide rep-guide--${mode}`}>
      {mode === 'intro' && <div className="rep-guide__title">怎样算一次</div>}
      <ol className="rep-guide__steps">
        {r.steps.map(([icon, text], i) => (
          <li key={i} className={i === warnStep ? 'warn' : i === active ? 'on' : ''} aria-current={i === active ? 'step' : undefined}>
            <span aria-hidden>{icon}</span>
            {text}
          </li>
        ))}
      </ol>
      {mode === 'intro' && <p className="rep-guide__miss">{r.miss}</p>}
      {mode === 'live' && r.gauge && progress != null && (
        <div className="rep-gauge" role="meter" aria-label={r.gauge.label} aria-valuemin={0} aria-valuemax={100}
          aria-valuenow={Math.round((100 * progress) / lineAt)}>
          <span className="rep-gauge__label">{r.gauge.label}</span>
          <div className="rep-gauge__track">
            <div className={`rep-gauge__fill${reached ? ' ok' : ''}`} style={{ width: pct(progress) }} />
            <div className="rep-gauge__line" style={{ left: pct(lineAt) }}>
              <span>{r.gauge.line}</span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
