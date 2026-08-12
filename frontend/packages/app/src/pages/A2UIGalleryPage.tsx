import { useEffect } from "react";
import { useProjectOutlet } from "@valuz/app/layout";
import { A2UIGallery } from "@valuz/a2ui/gallery";

/** Shared hidden developer surface; distributions contribute Gallery groups at runtime. */
export function A2UIGalleryPage() {
  const { setContentInnerClassName } = useProjectOutlet();

  useEffect(() => {
    setContentInnerClassName("p-0");
    return () => setContentInnerClassName(undefined);
  }, [setContentInnerClassName]);

  return <A2UIGallery embedded />;
}
