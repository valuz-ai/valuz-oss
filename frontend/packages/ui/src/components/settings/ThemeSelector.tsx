import { SegmentedControl } from "../ui/segmented-control";
import { useI18n } from "../../hooks/use-i18n";

export interface ThemeSelectorProps {
  theme: string;
  onChange: (theme: string) => void;
}

const optionKeys = ["light", "dark", "auto"] as const;

const keyMap: Record<
  string,
  "ui.themeSelector.light" | "ui.themeSelector.dark" | "ui.themeSelector.auto"
> = {
  light: "ui.themeSelector.light",
  dark: "ui.themeSelector.dark",
  auto: "ui.themeSelector.auto",
};

export const ThemeSelector = ({ theme, onChange }: ThemeSelectorProps) => {
  const { t } = useI18n();

  return (
    <SegmentedControl
      value={theme}
      onValueChange={onChange}
      className="h-8 w-[min(240px,52vw)] rounded-md p-0.5"
      buttonClassName="rounded-[5px]"
      options={optionKeys.map((key) => ({
        value: key,
        label: t(keyMap[key]),
      }))}
    />
  );
};
