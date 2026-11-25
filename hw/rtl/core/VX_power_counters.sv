// SPDX-License-Identifier: Apache-2.0
`include "VX_define.vh"

module VX_power_counters import VX_gpu_pkg::*; #(
    parameter COUNTER_BITS = PERF_CTR_BITS
) (
    input wire clk,
    input wire reset,
    input wire busy,
    input wire [`NUM_WARPS-1:0] active_warps,
    input wire [`NUM_WARPS-1:0][`NUM_THREADS-1:0] thread_masks,
    input wire decode_fire,
    input decode_t decoded,
    input wire [NUM_EX_UNITS*`ISSUE_WIDTH-1:0] dispatch_fire,
    input dispatch_t dispatched [NUM_EX_UNITS*`ISSUE_WIDTH],
    input wire [`ISSUE_WIDTH-1:0] writeback_valid,
    input writeback_t written [`ISSUE_WIDTH],
    output power_perf_t perf
);
    `STATIC_ASSERT(COUNTER_BITS > 0 && COUNTER_BITS <= PERF_CTR_BITS, ("invalid counter width"))
    `UNUSED_VAR(decoded)

    function automatic integer category(input integer unit_id, input dispatch_t data);
        logic unused_data;
    `ifdef EXT_F_ENABLE
        integer fp_base;
    `endif
        unused_data = ^data;
        case (unit_id)
        EX_ALU: begin
            case (data.op_args.alu.xtype)
            ALU_TYPE_BRANCH: return `VX_POWER_OP_BRANCH;
            ALU_TYPE_MULDIV: return data.op_type[2] ? `VX_POWER_OP_INT_DIV : `VX_POWER_OP_INT_MUL;
            default: return `VX_POWER_OP_INT_ALU;
            endcase
        end
        EX_LSU: begin
            if (inst_lsu_is_fence(data.op_type))
                return `VX_POWER_OP_FENCE;
            return data.op_args.lsu.is_store ? `VX_POWER_OP_STORE : `VX_POWER_OP_LOAD;
        end
        EX_SFU: return inst_sfu_is_csr(data.op_type) ? `VX_POWER_OP_CSR : `VX_POWER_OP_WARP_CONTROL;
    `ifdef EXT_F_ENABLE
        EX_FPU: begin
            fp_base = data.op_args.fpu.fmt[0] ? `VX_POWER_OP_FP64_ADD : `VX_POWER_OP_FP32_ADD;
            case (data.op_type)
            INST_FPU_ADD: return fp_base;
            INST_FPU_MUL: return fp_base + 1;
            INST_FPU_MADD, INST_FPU_NMADD: return fp_base + 2;
            INST_FPU_DIV: return fp_base + 3;
            INST_FPU_SQRT: return fp_base + 4;
            default: return fp_base + 5;
            endcase
        end
    `endif
    `ifdef EXT_TCU_ENABLE
        EX_TCU: return `VX_POWER_OP_TENSOR;
    `endif
        default: return `VX_POWER_OP_OTHER;
        endcase
    endfunction

    integer unsigned activity_delta [`VX_POWER_NUM_ACTIVITY];
    integer unsigned warp_delta [`VX_POWER_NUM_OPS];
    integer unsigned lane_delta [`VX_POWER_NUM_OPS];
    logic [`VX_POWER_NUM_OPS-1:0] issued;

    // Use the same FPGA population-count implementation as the datapath.
    wire [$clog2(`NUM_THREADS+1)-1:0] resident_lanes [`NUM_WARPS];
    wire [$clog2(`NUM_THREADS+1)-1:0] decoded_lanes;
    wire [$clog2(`SIMD_WIDTH+1)-1:0] dispatch_lanes [NUM_EX_UNITS*`ISSUE_WIDTH];
    wire [$clog2(`SIMD_WIDTH+1)-1:0] writeback_lanes [`ISSUE_WIDTH];
    for (genvar w = 0; w < `NUM_WARPS; ++w) begin : g_resident_lanes
        `POP_COUNT(resident_lanes[w], thread_masks[w]);
    end
    `POP_COUNT(decoded_lanes, decoded.tmask);
    for (genvar p = 0; p < NUM_EX_UNITS*`ISSUE_WIDTH; ++p) begin : g_dispatch_lanes
        `POP_COUNT(dispatch_lanes[p], dispatched[p].tmask);
    end
    for (genvar p = 0; p < `ISSUE_WIDTH; ++p) begin : g_writeback_lanes
        `POP_COUNT(writeback_lanes[p], written[p].tmask);
    end

    always @(*) begin
        for (integer i = 0; i < `VX_POWER_NUM_ACTIVITY; ++i)
            activity_delta[i] = 0;
        for (integer i = 0; i < `VX_POWER_NUM_OPS; ++i) begin
            warp_delta[i] = 0;
            lane_delta[i] = 0;
        end
        issued = '0;
        activity_delta[`VX_POWER_BUSY_CYCLES] = 32'(busy);
        for (integer w = 0; w < `NUM_WARPS; ++w) begin
            if (active_warps[w]) begin
                activity_delta[`VX_POWER_WARP_CYCLES] += 1;
                activity_delta[`VX_POWER_RESIDENT_THREAD_CYCLES] += 32'(resident_lanes[w]);
            end
        end
        if (decode_fire) begin
            activity_delta[`VX_POWER_DECODED_WARPS] = 1;
            for (integer s = 0; s < NUM_SRC_OPDS; ++s) begin
                if (decoded.used_rs[s])
                    activity_delta[`VX_POWER_LOGICAL_READ_WORDS] += 32'(decoded_lanes);
            end
        end
        for (integer p = 0; p < NUM_EX_UNITS*`ISSUE_WIDTH; ++p) begin
            logic [$clog2(`VX_POWER_NUM_OPS)-1:0] op_index;
            op_index = $clog2(`VX_POWER_NUM_OPS)'(category(p / `ISSUE_WIDTH, dispatched[p]));
            if (dispatch_fire[p]) begin
                warp_delta[op_index] += 32'(dispatched[p].sop);
                lane_delta[op_index] += 32'(dispatch_lanes[p]);
                issued[op_index] = 1;
            end
        end
        activity_delta[`VX_POWER_ISSUE_CYCLES] = 32'(|issued);
        for (integer i = 0; i < `VX_POWER_NUM_OPS; ++i) begin
            activity_delta[`VX_POWER_ISSUED_WARPS] += warp_delta[i];
            activity_delta[`VX_POWER_ISSUED_LANES] += lane_delta[i];
        end
        for (integer p = 0; p < `ISSUE_WIDTH; ++p) begin
            if (writeback_valid[p])
                activity_delta[`VX_POWER_WRITEBACK_WORDS] += 32'(writeback_lanes[p]);
        end
    end

    always @(posedge clk) begin
        if (reset) begin
            perf <= '0;
        end else begin
            for (integer i = 0; i < `VX_POWER_NUM_ACTIVITY; ++i)
                perf.activity[i] <= PERF_CTR_BITS'(COUNTER_BITS'(perf.activity[i] + PERF_CTR_BITS'(activity_delta[i])));
            for (integer i = 0; i < `VX_POWER_NUM_OPS; ++i) begin
                perf.warp_instructions[i] <= PERF_CTR_BITS'(COUNTER_BITS'(perf.warp_instructions[i] + PERF_CTR_BITS'(warp_delta[i])));
                perf.lane_instructions[i] <= PERF_CTR_BITS'(COUNTER_BITS'(perf.lane_instructions[i] + PERF_CTR_BITS'(lane_delta[i])));
                perf.issue_cycles[i] <= PERF_CTR_BITS'(COUNTER_BITS'(perf.issue_cycles[i] + PERF_CTR_BITS'(issued[i])));
            end
        end
    end
endmodule
