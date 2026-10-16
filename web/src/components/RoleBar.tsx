/**
 * @name 演示：切换身份
 * @description 路演时在同一台手机上依次展示「老人 / 子女 / 康复师」看到的页面。只是切换页面，
 * 子女和康复师能不能看到内容，仍然由老人的授权决定（未授权就是锁定页）。在老人端「隐私与设置」里打开。
 */

import { useEffect, useState } from 'react';
import { api } from '../api';

const KEY = 'jw.roles';

export function rolesOn(): boolean {
  try {
    return localStorage.getItem(KEY) === 'on';
  } catch {
    return false;
  }
}

export function setRolesOn(on: boolean) {
  try {
    localStorage.setItem(KEY, on ? 'on' : 'off');
  } catch {
    /* 记不住也没关系 */
  }
}

type Role = 'elder' | 'family' | 'therapist';

export function RoleBar({ current }: { current: Role }) {
  const [on, setOn] = useState(rolesOn);
  const [tok, setTok] = useState<{ family: string; therapist: string } | null>(null);
  const mirror = window.location.hash.includes('mirror');

  useEffect(() => {
    if (on && !mirror) api('/api/share').then((s) => setTok({ family: s.family_token, therapist: s.therapist_token })).catch(() => {});
  }, [on, mirror]);

  if (!on || mirror) return null;
  const go = (r: Role) => {
    if (r === current) return;
    window.location.hash = r === 'elder' ? '#/elder' : r === 'family' ? `#/family?token=${tok?.family || ''}` : `#/therapist?token=${tok?.therapist || ''}`;
  };
  return (
    <div className="rolebar" role="navigation" aria-label="演示：切换身份">
      <span className="rolebar__label">演示身份</span>
      {([['elder', '👵 老人'], ['family', '👨‍👩‍👧 子女'], ['therapist', '🩺 康复师']] as [Role, string][]).map(([r, t]) => (
        <button key={r} className={r === current ? 'on' : ''} aria-pressed={r === current} onClick={() => go(r)} disabled={!tok && r !== 'elder'}>
          {t}
        </button>
      ))}
      <button className="rolebar__x" aria-label="关闭演示切换" onClick={() => { setRolesOn(false); setOn(false); }}>×</button>
    </div>
  );
}
