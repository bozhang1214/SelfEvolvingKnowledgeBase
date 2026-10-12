/**
 * H8（工作流 G）· 通用的小 JSON 文件读写（设置项 / 会话都用它）。
 *
 * 与 `HistoryStore` 同一纪律：**先写 `.tmp` 再 `rename` 覆盖**——直接覆盖时进程若在中途被杀
 * 会留下半截 JSON，下次启动整个文件作废（丢设置比丢一次写入严重得多）。
 */
import { fileIo } from '@kit.CoreFileKit';
import { hilog } from '@kit.PerformanceAnalysisKit';
import { util } from '@kit.ArkTS';

const TAG: string = 'SEKB_HARMONY_UI';

export class JsonFile {
  private path: string;

  constructor(path: string) {
    this.path = path;
  }

  /** 读成文本；不存在/读失败返回空串（调用方自己决定回落值）。 */
  readText(): string {
    try {
      if (!fileIo.accessSync(this.path)) {
        return '';
      }
      const size: number = fileIo.statSync(this.path).size;
      if (size <= 0) {
        return '';
      }
      const buf = new ArrayBuffer(size);
      const rd = fileIo.openSync(this.path, fileIo.OpenMode.READ_ONLY);
      try {
        fileIo.readSync(rd.fd, buf);
      } finally {
        fileIo.closeSync(rd);
      }
      return util.TextDecoder.create('utf-8').decodeToString(new Uint8Array(buf));
    } catch (e) {
      hilog.error(0x0000, TAG, '读文件失败 %{public}s：%{public}s', this.path, JSON.stringify(e));
      return '';
    }
  }

  writeText(text: string): boolean {
    const tmp: string = `${this.path}.tmp`;
    try {
      const buf: ArrayBuffer = new util.TextEncoder().encodeInto(text).buffer as ArrayBuffer;
      const w = fileIo.openSync(tmp, fileIo.OpenMode.CREATE | fileIo.OpenMode.READ_WRITE | fileIo.OpenMode.TRUNC);
      try {
        fileIo.writeSync(w.fd, buf);
      } finally {
        fileIo.closeSync(w);
      }
      fileIo.renameSync(tmp, this.path);
      return true;
    } catch (e) {
      hilog.error(0x0000, TAG, '写文件失败 %{public}s：%{public}s', this.path, JSON.stringify(e));
      return false;
    }
  }

  exists(): boolean {
    try {
      return fileIo.accessSync(this.path);
    } catch (e) {
      return false;
    }
  }
}

/** 列出目录下的普通文件（文档页"导入"需要一个来源目录）。 */
export function listFiles(dir: string): string[] {
  const out: string[] = [];
  try {
    if (!fileIo.accessSync(dir)) {
      return out;
    }
    for (const name of fileIo.listFileSync(dir)) {
      out.push(name);
    }
  } catch (e) {
    hilog.error(0x0000, TAG, '列目录失败 %{public}s：%{public}s', dir, JSON.stringify(e));
  }
  return out;
}

/** 读原始字节数（文档页显示体积要的是**文件字节数**，不是字符数）。 */
export function fileSize(path: string): number {
  try {
    return fileIo.statSync(path).size;
  } catch (e) {
    return -1;
  }
}
