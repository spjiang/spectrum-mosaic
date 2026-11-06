// CudaSift 的进程内接口。一张卡、一个上下文，按张提取。

#include <cstring>

#include <cuda_runtime.h>

#include "cudaImage.h"
#include "cudaSift.h"

static int g_dev = -1;
static int g_w = 0;
static int g_h = 0;
static int g_max = 0;
static CudaImage *g_img = nullptr;
static SiftData g_data;
static float *g_tmp = nullptr;
static bool g_data_ready = false;

static void release_buffers() {
  if (g_tmp != nullptr) {
    FreeSiftTempMemory(g_tmp);
    g_tmp = nullptr;
  }
  if (g_data_ready) {
    FreeSiftData(g_data);
    g_data_ready = false;
  }
  delete g_img;
  g_img = nullptr;
  g_w = 0;
  g_h = 0;
  g_max = 0;
}

extern "C" int sift_init(int dev) {
  if (g_dev == dev) {
    return 0;
  }
  release_buffers();
  InitCuda(dev);
  g_dev = dev;
  return 0;
}

extern "C" int sift_extract(
    const float *host,
    int w,
    int h,
    int max_pts,
    float thresh,
    float *keypoints,
    float *descriptors,
    int *num_pts) {
  if (w < 1 || h < 1 || max_pts < 1 || host == nullptr) {
    return 1;
  }
  if (w != g_w || h != g_h || max_pts != g_max) {
    release_buffers();
    g_img = new CudaImage();
    g_img->Allocate(w, h, iAlignUp(w, 128), false, nullptr, nullptr);
    InitSiftData(g_data, max_pts, true, true);
    g_tmp = AllocSiftTempMemory(w, h, 5, false);
    g_w = w;
    g_h = h;
    g_max = max_pts;
    g_data_ready = true;
    if (g_tmp == nullptr) {
      return 2;
    }
  }
  g_img->h_data = const_cast<float *>(host);
  g_img->Download();
  ExtractSift(g_data, *g_img, 5, 1.0, thresh, 0.0f, false, g_tmp);
  int n = g_data.numPts;
  if (n < 0) {
    n = 0;
  }
  if (n > max_pts) {
    n = max_pts;
  }
  for (int i = 0; i < n; ++i) {
    const SiftPoint &p = g_data.h_data[i];
    keypoints[i * 4 + 0] = p.xpos;
    keypoints[i * 4 + 1] = p.ypos;
    keypoints[i * 4 + 2] = p.scale;
    keypoints[i * 4 + 3] = p.orientation;
    std::memcpy(descriptors + i * 128, p.data, sizeof(float) * 128);
  }
  *num_pts = n;
  return 0;
}
