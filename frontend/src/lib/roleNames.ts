// 角色码 → 中文名映射（后端 role 字段是英文码，前端统一转中文展示）
// 展示名以后端下发的 name/character_name 为准，此处仅做角色码翻译，不做名字猜测。

const ROLE_NAME_MAP: Record<string, string> = {
  // 通用
  receptionist: "前台接待",
  lawyer: "律师",
  judge: "法官",
  client: "当事人",
  // 刑事
  plaintiff: "委托方（家属）",
  plaintiff_lawyer: "辩护律师",
  defendant: "被告人",
  defendant_lawyer: "辩护律师",
  appellant: "上诉人（被告人）",
  appellee: "被上诉人",
  prosecutor: "检察官",
  investigator: "侦查人员",
  suspect: "犯罪嫌疑人",
  suspect_family: "嫌疑人家属",
  defense_lawyer: "辩护律师",
  criminal_judge: "刑事审判长",
  clerk: "书记员",
  bailiff: "法警",
};

/** 把后端角色码转成中文；未知码原样返回 */
export function roleName(role: string | undefined | null): string {
  if (!role) return "";
  const key = role.trim().toLowerCase();
  return ROLE_NAME_MAP[key] ?? role;
}

/** agent 展示名：优先后端下发的中文名（name/character_name），其次按角色码翻译；不猜测、不拼接 */
export function agentDisplayName(
  name: string | undefined | null,
  role?: string | undefined | null,
  characterName?: string | undefined | null,
): string {
  // 优先用后端下发的正式中文名（如"严某聪家属委托人"），character_name 精灵名仅作兜底
  const candidates = [name, characterName].filter(
    (v) => typeof v === "string" && v.trim().length > 0,
  );
  for (const candidate of candidates) {
    const trimmed = (candidate as string).trim();
    // 含中文视为后端已给出正式展示名
    if (/[一-鿿]/.test(trimmed)) return trimmed;
    // 纯角色码（如 receptionist）翻译
    const key = trimmed.toLowerCase();
    if (ROLE_NAME_MAP[key]) return ROLE_NAME_MAP[key];
  }
  if (role) return roleName(role);
  return "未知";
}
