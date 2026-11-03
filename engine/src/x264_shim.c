/* The engine's bridge to libx264: plain types across, x264.h as it is on this side. */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <x264.h>

typedef struct lp_x264 {
    x264_t *encoder;
    x264_param_t param;
    x264_picture_t picture;
    int force_key;
} lp_x264;

static void say(char *err, int len, const char *text, const char *what) {
    if (err && len > 0) snprintf(err, (size_t)len, "%s%s%s", text, what ? ": " : "", what ? what : "");
}

/* An encoder for I420 pictures of width x height: zero latency, one thread, one slice a
   picture, no B pictures, a keyframe every keyint pictures with its headers, CRF capped at
   kbit/s (VBV), the colours tagged BT.601 in limited or full range, told which macroblocks
   stayed the same (mb_info). extra: "name=value:name=value" of x264's own options. */
lp_x264 *lp_x264_open(int width, int height, int fps, float crf, int kbit, int keyint,
                      const char *preset, const char *profile, int full, const char *extra,
                      char *err, int err_len) {
    lp_x264 *e = calloc(1, sizeof *e);
    if (!e) { say(err, err_len, "out of memory", NULL); return NULL; }
    x264_param_t *p = &e->param;
    if (x264_param_default_preset(p, preset, "zerolatency") < 0) {
        say(err, err_len, "unknown preset", preset); free(e); return NULL;
    }
    p->i_threads = 1;
    p->b_sliced_threads = 0;
    p->i_width = width;
    p->i_height = height;
    p->i_csp = X264_CSP_I420;
    p->i_fps_num = fps;
    p->i_fps_den = 1;
    p->i_timebase_num = 1;
    p->i_timebase_den = fps;
    p->i_keyint_max = keyint;
    p->i_keyint_min = keyint;
    p->i_scenecut_threshold = 0;
    p->i_bframe = 0;
    p->rc.i_rc_method = X264_RC_CRF;
    p->rc.f_rf_constant = crf;
    p->rc.i_vbv_max_bitrate = kbit;
    p->rc.i_vbv_buffer_size = kbit;
    p->rc.f_ip_factor = 2.0f;
    p->b_repeat_headers = 1;
    p->b_annexb = 1;
    p->vui.b_fullrange = full ? 1 : 0;
    p->vui.i_colorprim = 6;  /* SMPTE 170M (BT.601) */
    p->vui.i_transfer = 6;
    p->vui.i_colmatrix = 6;
    p->analyse.b_mb_info = 1;
    p->analyse.b_mb_info_update = 0;
    p->analyse.b_psnr = 0;
    p->analyse.b_ssim = 0;
    p->i_log_level = X264_LOG_WARNING;
    for (const char *at = extra; at && *at;) {  /* name=value:name=value */
        const char *end = strchr(at, ':');
        size_t len = end ? (size_t)(end - at) : strlen(at);
        char item[128];
        if (len >= sizeof item) { say(err, err_len, "an x264 option too long", NULL); free(e); return NULL; }
        memcpy(item, at, len);
        item[len] = 0;
        at = end ? end + 1 : at + len;
        if (!len) continue;
        char *eq = strchr(item, '=');
        const char *value = "1";
        if (eq) { *eq = 0; value = eq + 1; }
        if (x264_param_parse(p, item, value) < 0) {
            say(err, err_len, "x264 does not take the option", item); free(e); return NULL;
        }
    }
    if (x264_param_apply_profile(p, profile) < 0) {
        say(err, err_len, "x264 does not take the profile", profile); free(e); return NULL;
    }
    e->encoder = x264_encoder_open(p);
    if (!e->encoder) { say(err, err_len, "x264 did not open", NULL); free(e); return NULL; }
    x264_encoder_parameters(e->encoder, p);
    x264_picture_init(&e->picture);
    e->picture.img.i_csp = X264_CSP_I420;
    e->picture.img.i_plane = 3;
    return e;
}

/* One picture: its planes, which macroblocks stayed the same (mb_info: X264_MBINFO_CONSTANT
   each, or NULL), its number. *out points at the picture's NAL units, one after the other, and
   the size is returned (0: none yet, negative: an error). */
int lp_x264_encode(lp_x264 *e, uint8_t *y, uint8_t *u, uint8_t *v, int stride_y, int stride_c,
                   uint8_t *mb_info, int64_t pts, const uint8_t **out, int *keyframe) {
    x264_picture_t *pic = &e->picture, done;
    pic->img.plane[0] = y;
    pic->img.plane[1] = u;
    pic->img.plane[2] = v;
    pic->img.i_stride[0] = stride_y;
    pic->img.i_stride[1] = stride_c;
    pic->img.i_stride[2] = stride_c;
    pic->i_pts = pts;
    pic->i_type = e->force_key ? X264_TYPE_IDR : X264_TYPE_AUTO;
    e->force_key = 0;
    pic->prop.mb_info = mb_info;
    pic->prop.mb_info_free = NULL;
    x264_nal_t *nal = NULL;
    int count = 0;
    int size = x264_encoder_encode(e->encoder, &nal, &count, pic, &done);
    if (size > 0 && count > 0) {
        *out = nal[0].p_payload;  /* (x264 keeps a picture's NAL units one after the other) */
        *keyframe = done.b_keyframe;
    } else {
        *out = NULL;
        *keyframe = 0;
    }
    return size;
}

/* The bit rate's cap changed in the running stream (VBV): no new encoder, no keyframe. */
int lp_x264_rate(lp_x264 *e, int kbit) {
    x264_param_t p = e->param;
    p.rc.i_vbv_max_bitrate = kbit;
    p.rc.i_vbv_buffer_size = kbit;
    int done = x264_encoder_reconfig(e->encoder, &p);
    if (done == 0) e->param = p;
    return done;
}

void lp_x264_keyframe(lp_x264 *e) { e->force_key = 1; }

void lp_x264_close(lp_x264 *e) {
    if (!e) return;
    if (e->encoder) x264_encoder_close(e->encoder);
    free(e);
}

int lp_x264_build(void) { return X264_BUILD; }
