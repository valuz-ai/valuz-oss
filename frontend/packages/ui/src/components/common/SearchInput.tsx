import { Search } from "lucide-react";
import { Input } from "../ui/input";
import { cn } from "../../lib/cn";
import { useI18n } from "../../hooks/use-i18n";

export interface SearchInputProps {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  className?: string;
}

/**
 * Text input with a built-in Search icon.
 */
export const SearchInput = ({
  value,
  onChange,
  placeholder,
  className,
}: SearchInputProps) => {
  const { t } = useI18n();
  const resolvedPlaceholder = placeholder ?? `${t("common.search")}...`;
  return (
    <div className={cn("relative", className)}>
      <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-ink-muted" />
      <Input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={resolvedPlaceholder}
        className="h-8 pl-8"
      />
    </div>
  );
};
