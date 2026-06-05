// 公共骨架只认识 'personal'。任何 enterprise edition id 都是不透明字符串，
// 由各 overlay 仓在自己的 profile 里声明，公共仓**永远不知道**它们的名字。
//
// 业务代码不允许做 `if (edition === '...')` 字面量分支判断；
// 差异必须通过 EditionProfile / Registry 表达。
// 只在极少数桥接位置（about 页显示、日志附带 edition tag）需要读 edition 值。

export type Edition = string;

export const PERSONAL_EDITION = "personal" as const;

export const isPersonalEdition = (edition: Edition): boolean =>
  edition === PERSONAL_EDITION;
