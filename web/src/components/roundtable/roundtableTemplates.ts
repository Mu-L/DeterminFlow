export interface SeatFormItem {
  role_name: string;
  system_prompt: string;
  temperature: number;
  is_moderator: boolean;
}

export const ROUNDTABLE_TEMPLATES = [
  {
    name: "技术评审",
    topic: "系统架构设计方案评审",
    strategy: "round_robin" as const,
    seats: [
      { role_name: "主持人", system_prompt: "你是技术评审会议的主持人。负责引导讨论，确保每个参与者都能发表意见。开场时简述讨论目标，收尾时总结共识和待办事项。", temperature: 0.3, is_moderator: true },
      { role_name: "架构师", system_prompt: "你是一位资深系统架构师，关注系统的可扩展性、性能、安全性和可维护性。从架构层面分析方案的优劣。", temperature: 0.7, is_moderator: false },
      { role_name: "开发工程师", system_prompt: "你是一位经验丰富的开发工程师，关注实现细节、开发效率、代码质量和技术债务。从落地角度评估方案的可行性。", temperature: 0.7, is_moderator: false },
    ],
  },
  {
    name: "头脑风暴",
    topic: "创新产品功能头脑风暴",
    strategy: "round_robin" as const,
    seats: [
      { role_name: "创意总监", system_prompt: "你是一位充满想象力的创意总监，善于提出颠覆性的创新想法。不受限于现有技术约束，大胆思考。", temperature: 1.0, is_moderator: false },
      { role_name: "产品经理", system_prompt: "你是一位务实的产品经理，关注用户需求、市场竞争和商业可行性。能够将创意转化为可执行的产品方案。", temperature: 0.7, is_moderator: false },
      { role_name: "用户体验专家", system_prompt: "你是一位用户体验专家，关注交互设计、用户情感和可用性。从用户视角审视每个想法的体验价值。", temperature: 0.8, is_moderator: false },
    ],
  },
  {
    name: "智能主持",
    topic: "AI 产品战略规划讨论",
    strategy: "moderator_decides" as const,
    seats: [
      { role_name: "AI 主持人", system_prompt: "你是一位智能会议主持人，负责引导讨论方向、分配发言机会、把控讨论节奏。你会根据讨论进展动态调整议程。", temperature: 0.3, is_moderator: true },
      { role_name: "技术专家", system_prompt: "你是 AI 技术领域的资深专家，关注技术可行性、创新机会和技术风险。从技术视角为产品战略提供支撑。", temperature: 0.7, is_moderator: false },
      { role_name: "商业分析师", system_prompt: "你是一位商业分析师，关注市场趋势、竞争格局和商业模式。从商业价值角度评估战略方向。", temperature: 0.7, is_moderator: false },
    ],
  },
];

export type RoundtableTemplate = (typeof ROUNDTABLE_TEMPLATES)[number];
