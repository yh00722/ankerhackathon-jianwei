/**
 * @name 统计数字
 * @description 数字大、单位小，整体不折行（「1 小时 5 分」「不到 1 分钟」「12」），用在老人端「今天」和子女端的数字行。
 */

import { Fragment } from 'react';

export function StatValue({ text }: { text: string | number }) {
  const parts = String(text).split(/(\d+(?:\.\d+)?)/).filter((p) => p.trim() !== '');
  return (
    <b className="stat-v">
      {parts.map((p, i) => (/^\d/.test(p) ? <Fragment key={i}>{p}</Fragment> : <small key={i}>{p.trim()}</small>))}
    </b>
  );
}
