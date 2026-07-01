// Gateway RPC handlers for durable routine registry operations.
import {
  ErrorCodes,
  errorShape,
  formatValidationErrors,
  validateRoutinesCreateParams,
  validateRoutinesDeleteParams,
  validateRoutinesGetParams,
  validateRoutinesListParams,
  validateRoutinesSetEnabledParams,
} from "../../../packages/gateway-protocol/src/index.js";
import { assertCronDeliveryInputNonBlankFields } from "../../cron/delivery-target-validation.js";
import { formatErrorMessage } from "../../infra/errors.js";
import {
  createRoutine,
  deleteRoutine,
  inspectRoutine,
  listRoutines,
  setRoutineEnabled,
  type RoutineCreateInput,
} from "../../routines/service.js";
import { assertValidCronCreateDelivery } from "./cron.js";
import type { GatewayRequestHandlers, RespondFn } from "./types.js";

function respondInvalid(respond: RespondFn, method: string, message: string): void {
  respond(
    false,
    undefined,
    errorShape(ErrorCodes.INVALID_REQUEST, `invalid ${method}: ${message}`),
  );
}

function respondValidationFailure(
  respond: RespondFn,
  method: string,
  errors: Parameters<typeof formatValidationErrors>[0],
): void {
  respondInvalid(respond, method, formatValidationErrors(errors));
}

export const routinesHandlers: GatewayRequestHandlers = {
  "routines.list": async ({ params, respond, context }) => {
    if (!validateRoutinesListParams(params)) {
      respondValidationFailure(respond, "routines.list", validateRoutinesListParams.errors);
      return;
    }
    const result = await listRoutines(params, {
      cron: context.cron,
      cronStorePath: context.cronStorePath,
    });
    respond(true, result);
  },
  "routines.get": async ({ params, respond, context }) => {
    if (!validateRoutinesGetParams(params)) {
      respondValidationFailure(respond, "routines.get", validateRoutinesGetParams.errors);
      return;
    }
    const routine = await inspectRoutine(params.id, {
      cron: context.cron,
      cronStorePath: context.cronStorePath,
    });
    respond(true, { routine: routine ?? null });
  },
  "routines.create": async ({ params, respond, context }) => {
    if (!validateRoutinesCreateParams(params)) {
      respondValidationFailure(respond, "routines.create", validateRoutinesCreateParams.errors);
      return;
    }
    const input = params as RoutineCreateInput;
    try {
      const result = await createRoutine(input, {
        cron: context.cron,
        cronStorePath: context.cronStorePath,
        validateCronCreate: async (cronInput) => {
          assertCronDeliveryInputNonBlankFields(cronInput.delivery);
          await assertValidCronCreateDelivery(context.getRuntimeConfig(), cronInput);
        },
      });
      context.logGateway.info("routines: routine created", {
        routineId: result.routine.id,
        cronJobId: result.routine.trigger.cronJobId,
        idempotent: result.idempotent,
      });
      respond(true, result);
    } catch (err) {
      respondInvalid(respond, "routines.create", formatErrorMessage(err));
    }
  },
  "routines.enable": async ({ params, respond, context }) => {
    if (!validateRoutinesSetEnabledParams(params)) {
      respondValidationFailure(respond, "routines.enable", validateRoutinesSetEnabledParams.errors);
      return;
    }
    try {
      respond(
        true,
        await setRoutineEnabled(params.id, true, {
          cron: context.cron,
          cronStorePath: context.cronStorePath,
        }),
      );
    } catch (err) {
      respondInvalid(respond, "routines.enable", formatErrorMessage(err));
    }
  },
  "routines.disable": async ({ params, respond, context }) => {
    if (!validateRoutinesSetEnabledParams(params)) {
      respondValidationFailure(
        respond,
        "routines.disable",
        validateRoutinesSetEnabledParams.errors,
      );
      return;
    }
    try {
      respond(
        true,
        await setRoutineEnabled(params.id, false, {
          cron: context.cron,
          cronStorePath: context.cronStorePath,
        }),
      );
    } catch (err) {
      respondInvalid(respond, "routines.disable", formatErrorMessage(err));
    }
  },
  "routines.delete": async ({ params, respond, context }) => {
    if (!validateRoutinesDeleteParams(params)) {
      respondValidationFailure(respond, "routines.delete", validateRoutinesDeleteParams.errors);
      return;
    }
    try {
      respond(
        true,
        await deleteRoutine(params.id, {
          cron: context.cron,
          cronStorePath: context.cronStorePath,
        }),
      );
    } catch (err) {
      respondInvalid(respond, "routines.delete", formatErrorMessage(err));
    }
  },
};
