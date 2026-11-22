cbuffer FrameInput : register(b0)
{
    float4 frame_color;
};

float4 main() : SV_Target
{
    return frame_color;
}
