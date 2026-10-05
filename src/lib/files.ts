import type { DebugAttachment } from "./types";

export const DOCUMENT_FILE_ACCEPT = ".md,.txt,.rst,.pdf,.docx,.pptx,.csv,.xlsx,.html,.htm,.json,.xml,.yaml,.yml,.py,.js,.ts,.tsx,.jsx,.java,.go,.rs,.c,.cpp,.h,.cs,.sh,.sql,.rb,.php,.eml,.log,.srt,.vtt,.png,.jpg,.jpeg,.tif,.tiff,.wav,.mp3,.mp4,.webm";
export const DEBUG_FILE_ACCEPT = ".txt,.md,.rst,.py,.js,.jsx,.ts,.tsx,.json,.yaml,.yml,.toml,.sql,.log,.sh,.css,.html,.go,.rs,.java,.c,.cpp,.h";
export const MAX_DEBUG_FILES = 5;
const MAX_FILE_BYTES = 50_000;

export async function readDebugFile(file: File): Promise<DebugAttachment> {
  const extension = file.name.slice(file.name.lastIndexOf(".")).toLowerCase();
  if (!DEBUG_FILE_ACCEPT.split(",").includes(extension)) {
    throw new Error(`${file.name}: attach a text or source-code file. Upload PDFs on Knowledge Sources.`);
  }
  if (file.size > MAX_FILE_BYTES) throw new Error(`${file.name}: debug attachments must be 50 KB or smaller.`);
  const content = await new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(new Error(`Could not read ${file.name}.`));
    reader.readAsText(file);
  });
  if (!content.trim() || content.includes(String.fromCharCode(0)) || content.includes("\uFFFD")) {
    throw new Error(`${file.name}: use a nonempty UTF-8 text file.`);
  }
  return { name: file.name, content };
}
