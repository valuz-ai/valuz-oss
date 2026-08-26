import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { useRegistryStore } from "@valuz/core";
import {
  RemoteAgentDetailActionSlot,
  ResourceCopyMenuItemSlot,
  ResourceDetailActionSlot,
} from "./ResourceActionSlot";

describe("ResourceDetailActionSlot", () => {
  afterEach(() => {
    act(() => {
      useRegistryStore
        .getState()
        .unregisterSlot("resource.agent.detail.actions", "test-detail-action");
      useRegistryStore
        .getState()
        .unregisterSlot(
          "resource.agent.copy.menu-items",
          "test-copy-menu-item",
        );
      useRegistryStore
        .getState()
        .unregisterSlot(
          "resource.agent.remote-detail.actions",
          "test-remote-action",
        );
    });
  });

  it("renders detail-only registrations with the resource context", () => {
    act(() => {
      useRegistryStore
        .getState()
        .registerSlot("resource.agent.detail.actions", {
          id: "test-detail-action",
          component: (props) => (
            <span>
              {String(props.resourceType)}:
              {String(
                (props.resource as Record<string, unknown> | undefined)?.slug,
              )}
            </span>
          ),
        });
    });

    render(
      <ResourceDetailActionSlot
        resourceType="agent"
        resource={{ slug: "course-builder" }}
      />,
    );

    expect(screen.getByText("agent:course-builder")).not.toBeNull();
  });

  it("renders copy-menu registrations with the resource context", () => {
    act(() => {
      useRegistryStore
        .getState()
        .registerSlot("resource.agent.copy.menu-items", {
          id: "test-copy-menu-item",
          component: (props) => (
            <span>
              Copy to {String(
                (props.resource as Record<string, unknown> | undefined)?.slug,
              )}
            </span>
          ),
        });
    });

    render(
      <ResourceCopyMenuItemSlot
        resourceType="agent"
        resource={{ slug: "course-builder" }}
      />,
    );

    expect(screen.getByText("Copy to course-builder")).not.toBeNull();
  });

  it("isolates remote detail actions from list-only activators", () => {
    act(() => {
      useRegistryStore
        .getState()
        .registerSlot("resource.agent.remote-detail.actions", {
          id: "test-remote-action",
          component: (props) => (
            <span>
              Download{" "}
              {String(
                (props.resource as Record<string, unknown> | undefined)?.slug,
              )}
            </span>
          ),
        });
    });

    render(
      <RemoteAgentDetailActionSlot resource={{ slug: "course-builder" }} />,
    );

    expect(screen.getByText("Download course-builder")).not.toBeNull();
  });
});
