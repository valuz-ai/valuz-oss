import { PERSONAL_PORTS } from "@valuz/shared";

export type DesktopServiceKind = "agent_server" | "plugin";

// Slice 3：edition 字段已删——@valuz/core 的 ServiceDescriptor 同步删除了同字段，
// 两边类型现在通过结构兼容（DesktopServiceDescriptor 只比 ServiceDescriptor 多个 optional kind）。
// Slice 6 会把这两个 interface 合并成一个。
export interface DesktopServiceDescriptor {
  name: string;
  kind?: DesktopServiceKind;
  defaultPort: number;
  requiredForBoot: boolean;
}

export const personalDescriptors = (): DesktopServiceDescriptor[] => [
  {
    name: "agent-server",
    kind: "agent_server",
    defaultPort: PERSONAL_PORTS.AGENT_SERVER,
    requiredForBoot: true,
  },
];

export class DescriptorRegistry {
  private readonly descriptors: DesktopServiceDescriptor[];

  constructor(initial: DesktopServiceDescriptor[]) {
    this.descriptors = [...initial];
  }

  snapshot(): DesktopServiceDescriptor[] {
    return [...this.descriptors];
  }

  register(descriptor: DesktopServiceDescriptor): DesktopServiceDescriptor {
    const index = this.descriptors.findIndex(
      (item) => item.name === descriptor.name,
    );

    if (index === -1) {
      this.descriptors.push(descriptor);
      return descriptor;
    }

    this.descriptors[index] = descriptor;
    return descriptor;
  }

  unregister(name: string): boolean {
    const index = this.descriptors.findIndex((item) => item.name === name);

    if (index === -1) {
      return false;
    }

    this.descriptors.splice(index, 1);
    return true;
  }
}
