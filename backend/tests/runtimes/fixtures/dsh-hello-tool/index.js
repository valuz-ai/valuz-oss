// A standard third-party dsh plugin: one tool registered on the shared tool registry.
export const name = 'dsh-hello-tool'
export const inject = ['tools']
export function apply(ctx) {
  ctx.effect(() => ctx.tools.register({
    name: 'hello_valuz',
    description: 'Return a greeting from a standard dsh plugin.',
    parameters: { type: 'object', properties: {}, additionalProperties: false },
    output: { schema: { type: 'string' }, render: (_args, value) => [{ type: 'text', text: String(value) }] },
    async execute() { return 'hello from a standard dsh plugin' },
  }))
}
