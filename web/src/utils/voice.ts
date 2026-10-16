/**
 * @name 老人端语音与振动
 * @description 手机 App（WebView）里用原生接口 window.JianweiNative（系统语音合成 + 振动）；
 * 普通浏览器里退回网页的 speechSynthesis / navigator.vibrate。都不可用时静默，页面上的大字照常显示。
 */

import { speak as webSpeak } from '../api';

interface NativeBridge {
  speak?: (text: string) => void;
  vibrate?: (ms: number) => void;
  ready?: () => boolean;
}

function native(): NativeBridge | null {
  return ((window as any).JianweiNative as NativeBridge | undefined) || null;
}

/** 手机 App 里且系统有中文语音 */
export function nativeVoiceReady(): boolean {
  try {
    const n = native();
    return !!n?.speak && (n.ready ? n.ready() : true);
  } catch {
    return false;
  }
}

export function say(text: string) {
  if (!text) return;
  try {
    const n = native();
    if (n?.speak) {
      n.speak(text);
      return;
    }
  } catch {
    /* 原生接口出错就退回网页语音 */
  }
  webSpeak(text);
}

export function buzz(ms = 60) {
  try {
    const n = native();
    if (n?.vibrate) n.vibrate(ms);
    else navigator.vibrate?.(ms);
  } catch {
    /* 没有振动也不影响 */
  }
}

const KEY = 'jw.elderVoice';

export function loadElderVoice(): boolean {
  try {
    return localStorage.getItem(KEY) !== 'off';
  } catch {
    return true;
  }
}

export function saveElderVoice(on: boolean) {
  try {
    localStorage.setItem(KEY, on ? 'on' : 'off');
  } catch {
    /* 记不住也没关系 */
  }
}
