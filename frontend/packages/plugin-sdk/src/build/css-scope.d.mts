/** ``[data-valuz-ext="<pluginId>"]`` */
export declare function scopeSelector(pluginId: string): string;
/** Prefix every selector of ``css`` with the plugin's scope (idempotent). */
export declare function scopeCss(css: string, pluginId: string): string;
