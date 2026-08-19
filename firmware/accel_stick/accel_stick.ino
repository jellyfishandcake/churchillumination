// Handheld M5Stack accelerometer stick - reads the board's built-in IMU and
// prints one normalised acceleration reading per line over USB-serial, for
// src/sensing/accel_stick.py on the Pi side to read.
//
// Uses M5Unified (https://github.com/m5stack/M5Unified) rather than a
// chip-specific IMU library (e.g. MPU6886) - M5Unified auto-detects which
// IMU chip is actually on the board (MPU6886, BMI270, SH200Q, ...) via
// M5.Imu.getType(), so this sketch works unmodified across M5StickC,
// M5StickC Plus2, M5Stack CoreS3, etc. without committing to one specific
// model.
//
// Wire protocol (see accel_stick.py's docstring for the Pi-side contract):
//   {"acceleration": 0.3, "angle_deg": 210.0, "battery_pct": 87}\n
// One JSON object per line, "acceleration" in [0, 1] - same "deviation from
// 1g at rest" convention the Pi-side sensors use (see the old sense_hat.py).
// "angle_deg" in [0, 360) - which horizontal direction the stick is
// currently being swung toward, in a WORLD frame fixed at power-on (see
// the "version on 2026-08-19" note in section 1 below for why this isn't
// simply atan2 of the two horizontal body axes any more, and what that
// change does/doesn't fix). Still a snapshot of current swing direction,
// not a true integrated position trajectory - good enough for "which of
// several directions did you just shake it toward" (see TriArmGlideEffect
// on the Pi side), not for tracking absolute position over time. At rest
// (no horizontal deviation) this is arbitrary/noisy - callers should gate
// on "acceleration" being above their own idle threshold before trusting
// it, same as before.
// "battery_pct" is extra - accel_stick.py only reads "acceleration" today
// (see its json.loads(...)["acceleration"] line), so this field is ignored
// Pi-side for now, not a breaking change - just future-proofing for a
// low-battery warning on the admin dashboard later.
//
// Setup:
//   1. Arduino IDE > Boards Manager > install "M5Stack" (adds ESP32S3 board
//      defs for M5StickC/CoreS3/etc).
//   2. Library Manager > install "M5Unified".
//   3. Select your board + port, upload.
//   4. Point src/sensing/accel_stick.py's serial_port config at this
//      board's port (e.g. /dev/ttyUSB0 on the Pi, COMx on Windows).
//
// Three independent features live below, each in its own section so any one
// of them can be pulled out or disabled without touching the others:
//   1. Core acceleration read + send (the only thing accel_stick.py needs)
//   2. Battery level reporting
//   3. On-device screen feedback (a live bar for whoever's holding it)
//   4. Idle power save (screen sleep after a stretch of no motion)

// version on 2026-08-04 - direction reporting switched from a 1D signed
// dominant-axis lean to a true atan2-based angle_deg, for the 3-arm zone

#include <M5Unified.h>
#include <math.h>

// Bundles the two numbers derived from one IMU read, so callers don't
// re-read the IMU twice per tick just to get both. Deliberately declared
// here, before every function in this file rather than immediately above
// readAcceleration() where it's actually used - Arduino's auto-generated
// function prototypes get hoisted to the very top of the sketch (above
// ALL function definitions, regardless of where those functions or the
// types they use actually appear textually), so any custom struct used as
// a parameter/return type has to be declared before the first function in
// the file or that hoisted prototype references a type the compiler
// hasn't seen yet ("AccelReading does not name a type" - hit exactly this
// once rotateByQuat/quatMultiply's reference parameters below changed how
// Arduino's ctags-based scanner parses the rest of the file).
struct AccelReading {
  float acceleration;  // [0, 1] - shake magnitude, unsigned
  float angle_deg;     // [0, 360) - which way it's currently swinging, WORLD frame (see version note below)
};

// ---------------------------------------------------------------------------
// 1. Core acceleration read + send
// ---------------------------------------------------------------------------

// One axis reads ~1g from gravity alone at rest - deviation from that
// baseline is what counts as "movement". Same convention as SenseHatSensor
// used to compute acceleration on the Pi side.
static constexpr float BASELINE_G = 1.0f;

// version on 2026-08-19 - angle_deg switched from a raw per-tick atan2 on
// BODY-frame accel to a gyro+accel-fused (Madgwick, 6DOF IMU-only) WORLD-
// frame angle. Real-hardware feedback: the raw version made the stick's
// current grip rotation/tilt matter as much as the actual swing direction
// - nobody holds a stick perfectly rigid through a swing, so the same
// physical gesture with a bit of incidental wrist rotation mid-swing read
// as a different angle_deg, because atan2 was reading straight off
// body-frame ax/ay with nothing correcting for how the body frame itself
// was rotating *during* the swing. Fusing in the gyro (see
// madgwickUpdateIMU below) tracks the stick's actual orientation through
// the motion and rotates the raw acceleration into a frame fixed at
// power-on before computing the angle, so a given real-world swing
// direction reads consistently regardless of incidental rotation while
// swinging.
//
// UPDATED same day once real hardware was confirmed: this specific board
// (StickS3) DOES have a magnetometer (BMI270 + BMM150) - see the separate
// "Magnetometer yaw correction" section further below, which corrects the
// gyro+accel-only yaw estimate below against it. With that section active,
// "0 degrees" is no longer purely "wherever the stick pointed at
// power-on" - it's continuously nudged back toward whatever the room's
// ambient magnetic field direction was when calibration first saw enough
// range (see MAG_CAL_MIN_RANGE), which does NOT drift over a session and
// mostly self-corrects across a power-cycle even in a different starting
// pose (not a true compass heading - no declination correction, doesn't
// need to be, see that section's own comment for why). The roll/pitch
// tracking and the "rotation during an active swing doesn't corrupt the
// reading" fix described above are unaffected either way, magnetometer or
// not. ARM_ANGLES_DEG below still needs a fresh on-site calibration pass
// after flashing this, since "0 degrees" means something different than
// it did under the old raw-atan2 version regardless.
//
// Reimplements Madgwick's widely-published open-source gradient-descent
// AHRS algorithm (Madgwick, S.O.H., "An efficient orientation filter for
// inertial and inertial/magnetic sensor arrays", 2010), IMU-only (gyro +
// accel, no magnetometer) variant, directly rather than pulling in a whole
// new Arduino library dependency this late - the update step below is the
// standard published equations, not a novel derivation. The magnetometer
// correction further below is deliberately NOT the same paper's full
// MARG/9-DOF extension (that combined gradient has a long magnetometer
// term that's easy to mistranscribe from memory with no independent way
// to check it here) - see that section's own comment for the simpler,
// independently-checkable complementary-filter approach used instead.

// Multiplies the raw g-deviation before clamping to [0, 1]. Was 1.0 (an
// untuned placeholder) - lowered after real-hardware feedback: at 1.0, a
// full [0,1] range needs a full 1g deviation from baseline (magnitude
// reaching 2g or 0g), which an ordinary hand-shake blows past almost
// instantly (real shakes routinely spike to 2-3g+ momentarily) - so both
// the screen bar (section 3) and the acceleration value itself pinned to
// max for nearly any real shake instead of scaling smoothly. Lower value
// = harder shake needed to reach 1.0, same [0, 1] contract either way for
// accel_stick.py and main.py's MOTION_BURST_THRESHOLD - still a
// placeholder in the sense that the exact number is feel, not physics,
// just a much closer starting point than 1.0 was.
static constexpr float SENSITIVITY = 0.35f;

// ~20Hz while actively held, matching the Pi's own sensor_loop rate. Slows
// down automatically while idle - see section 4.
static constexpr unsigned long ACTIVE_SEND_INTERVAL_MS = 50;

// BETA is the filter's convergence gain - higher trusts the accelerometer
// more (converges/corrects faster, but noisier); lower trusts the gyro
// more (smoother, but slower to correct drift). 0.1 is the commonly-used
// default for this algorithm - UNTUNED against this specific hardware and
// ~20Hz sample rate, same "feel, not physics, verify on real hardware"
// caveat as every other threshold in this file (see SENSITIVITY above).
static constexpr float MADGWICK_BETA = 0.1f;

// Local degrees->radians constant rather than relying on Arduino core's
// DEG_TO_RAD macro being defined on every board/core version this file
// might get compiled against - one less thing to go wrong on a first flash.
static constexpr float DEG_TO_RAD_F = (float)M_PI / 180.0f;

// Orientation quaternion, body frame -> world frame, identity (i.e. "world
// == body") at power-on/first read - see the "version on 2026-08-19"
// comment above for what this world frame is and isn't anchored to.
static float q0 = 1.0f, q1 = 0.0f, q2 = 0.0f, q3 = 0.0f;

// One gradient-descent correction step of Madgwick's IMU-only (no
// magnetometer) algorithm - see the version comment above for what this
// is/where it's from. gx/gy/gz in rad/s; ax/ay/az any consistent unit
// (normalised internally, so g is fine); dt in seconds. Updates the
// q0..q3 orientation state in place.
void madgwickUpdateIMU(float gx, float gy, float gz, float ax, float ay, float az, float dt) {
  float qDot1 = 0.5f * (-q1 * gx - q2 * gy - q3 * gz);
  float qDot2 = 0.5f * (q0 * gx + q2 * gz - q3 * gy);
  float qDot3 = 0.5f * (q0 * gy - q1 * gz + q3 * gx);
  float qDot4 = 0.5f * (q0 * gz + q1 * gy - q2 * gx);

  // Skip the accelerometer correction step on a degenerate (all-zero)
  // reading - shouldn't happen with a live sensor, but would NaN the
  // normalisation below if it ever did.
  if (!(ax == 0.0f && ay == 0.0f && az == 0.0f)) {
    float recipNorm = 1.0f / sqrtf(ax * ax + ay * ay + az * az);
    ax *= recipNorm; ay *= recipNorm; az *= recipNorm;

    float _2q0 = 2.0f * q0, _2q1 = 2.0f * q1, _2q2 = 2.0f * q2, _2q3 = 2.0f * q3;
    float _4q0 = 4.0f * q0, _4q1 = 4.0f * q1, _4q2 = 4.0f * q2;
    float _8q1 = 8.0f * q1, _8q2 = 8.0f * q2;
    float q0q0 = q0 * q0, q1q1 = q1 * q1, q2q2 = q2 * q2, q3q3 = q3 * q3;

    float s0 = _4q0 * q2q2 + _2q2 * ax + _4q0 * q1q1 - _2q1 * ay;
    float s1 = _4q1 * q3q3 - _2q3 * ax + 4.0f * q0q0 * q1 - _2q0 * ay - _4q1 + _8q1 * q1q1 + _8q1 * q2q2 + _4q1 * az;
    float s2 = 4.0f * q0q0 * q2 + _2q0 * ax + _4q2 * q3q3 - _2q3 * ay - _4q2 + _8q2 * q1q1 + _8q2 * q2q2 + _4q2 * az;
    float s3 = 4.0f * q1q1 * q3 - _2q1 * ax + 4.0f * q2q2 * q3 - _2q2 * ay;
    recipNorm = 1.0f / sqrtf(s0 * s0 + s1 * s1 + s2 * s2 + s3 * s3);
    s0 *= recipNorm; s1 *= recipNorm; s2 *= recipNorm; s3 *= recipNorm;

    qDot1 -= MADGWICK_BETA * s0;
    qDot2 -= MADGWICK_BETA * s1;
    qDot3 -= MADGWICK_BETA * s2;
    qDot4 -= MADGWICK_BETA * s3;
  }

  q0 += qDot1 * dt; q1 += qDot2 * dt; q2 += qDot3 * dt; q3 += qDot4 * dt;

  float recipNorm = 1.0f / sqrtf(q0 * q0 + q1 * q1 + q2 * q2 + q3 * q3);
  q0 *= recipNorm; q1 *= recipNorm; q2 *= recipNorm; q3 *= recipNorm;
}

// Rotates a body-frame vector by an arbitrary unit quaternion (qw,qx,qy,qz)
// via the standard unit-quaternion-to-rotation-matrix expansion. Takes the
// quaternion explicitly (rather than reading the global q0..q3 directly)
// so magYawCorrect further below can reuse this for a different,
// temporary yaw-zeroed quaternion without disturbing the real orientation
// state.
void rotateByQuat(float qw, float qx, float qy, float qz, float vx, float vy, float vz, float &wx, float &wy, float &wz) {
  wx = (1 - 2 * (qy * qy + qz * qz)) * vx + 2 * (qx * qy - qw * qz) * vy + 2 * (qx * qz + qw * qy) * vz;
  wy = 2 * (qx * qy + qw * qz) * vx + (1 - 2 * (qx * qx + qz * qz)) * vy + 2 * (qy * qz - qw * qx) * vz;
  wz = 2 * (qx * qz - qw * qy) * vx + 2 * (qy * qz + qw * qx) * vy + (1 - 2 * (qx * qx + qy * qy)) * vz;
}

// Rotates a body-frame vector into the world frame established at
// power-on (and continuously yaw-corrected by magYawCorrect below). Used
// to project the raw (gravity + swing) acceleration reading into a frame
// that doesn't rotate with however the stick's currently being gripped.
void rotateBodyToWorld(float vx, float vy, float vz, float &wx, float &wy, float &wz) {
  rotateByQuat(q0, q1, q2, q3, vx, vy, vz, wx, wy, wz);
}

// Hamilton product a (x) b of two unit quaternions, written out rather
// than looped for the same "cheap to eyeball-verify" reason as everything
// else here. Used by magYawCorrect below to compose a small extra yaw
// correction onto the existing orientation quaternion.
void quatMultiply(float a0, float a1, float a2, float a3, float b0, float b1, float b2, float b3, float &r0, float &r1, float &r2, float &r3) {
  r0 = a0 * b0 - a1 * b1 - a2 * b2 - a3 * b3;
  r1 = a0 * b1 + a1 * b0 + a2 * b3 - a3 * b2;
  r2 = a0 * b2 - a1 * b3 + a2 * b0 + a3 * b1;
  r3 = a0 * b3 + a1 * b2 - a2 * b1 + a3 * b0;
}

// ---------------------------------------------------------------------------
// Magnetometer yaw correction (StickS3 only - confirmed BMI270 + BMM150,
// see the "version on 2026-08-19" comment above)
// ---------------------------------------------------------------------------
// madgwickUpdateIMU above has no way to know true yaw on its own: gyro-only
// yaw is pure dead reckoning (integrate and hope), which drifts, and left
// "0 degrees" as nothing more than whatever the stick's own +X axis
// pointed at power-on. The magnetometer gives an independent, non-drifting
// reference to correct yaw against - NOT true compass north (no
// declination correction, no interest in an absolute geographic heading
// here), just "whatever the room's ambient magnetic field direction was
// when this session's calibration first saw enough range," which is all a
// *stable* reference needs to be for "which of a few arms did you swing
// toward."
//
// Deliberately kept separate from madgwickUpdateIMU's gradient-descent
// accel correction above, rather than folding mag into one combined 9-DOF
// gradient (the textbook "Madgwick MARG" / "Mahony 9-DOF" formulas) - the
// combined versions are correct but their magnetometer terms are a long,
// easy-to-mistranscribe block with no independent way to sanity-check
// short of a second working reference to diff against, which isn't
// available here. This instead reuses pieces already written and
// individually checkable above (rotateByQuat, quatMultiply) plus the
// standard, widely-published, much shorter quaternion<->Euler formulas
// below, composed as an ordinary complementary filter: gyro+accel handle
// fast/short-term orientation (unchanged, above), the magnetometer nudges
// yaw specifically, slowly, independently.

// Gain on the proportional yaw nudge, in radians/sec of correction per
// radian of measured error (a full-circle disagreement corrects at
// MAG_YAW_GAIN rad/s) - untuned against real hardware, like every other
// gain in this file. Deliberately small: this should converge over several
// seconds, not fight the gyro's instant-by-instant tracking during an
// actual swing - a momentary mag/gyro disagreement mid-swing is far more
// likely to be swing-induced magnetic noise than genuine yaw drift.
static constexpr float MAG_YAW_GAIN = 0.3f;

// DISABLED 2026-08-19 after real-hardware testing: instead of a stable
// reference, "cannot seem to find a set coordinate system" - angle_deg
// kept moving even without the stick's real orientation changing. Leading
// suspect: BMM150 sits behind BMI270, on a separate die/PCB placement, not
// necessarily axis-aligned with it the way this code assumed (rotateByQuat
// applies the same body frame to both mx/my/mz and ax/ay/az/gx/gy/gz with
// no remapping) - if the mag axes are actually rotated/mirrored relative
// to the IMU's, magYawCorrect's "error" term is comparing two headings
// that were never in the same frame to begin with, and every tick nudges
// the real (previously working) 6DOF orientation toward that bogus error
// instead of correcting real drift. Flip this back to true only after
// independently confirming BMM150's axis mapping against BMI270's (e.g.
// log raw mx/my/mz while rotating the stick through known 90-degree turns
// about each axis and check which axis actually responds) - not safe to
// guess at with no hardware in hand to verify against.
static constexpr bool MAG_YAW_CORRECTION_ENABLED = false;

// Running hard-iron calibration (min/max per axis -> offset = midpoint).
// No soft-iron/scale correction - offset-only is a rough-but-usable
// compass for "which of a few directions," not lab-grade heading accuracy.
// Passive: updates from whatever orientations the stick naturally passes
// through during normal handling, rather than a dedicated "wave it in a
// figure-8" calibration step - simpler, at the cost of the correction
// being weak/absent right after power-on until it's been moved around
// enough to see real range on each axis (guarded by MAG_CAL_MIN_RANGE).
static float mag_min[3] = {1e6f, 1e6f, 1e6f};
static float mag_max[3] = {-1e6f, -1e6f, -1e6f};
static constexpr float MAG_CAL_MIN_RANGE = 5.0f;  // unit-agnostic (whatever M5.Imu.getMag()'s own units are) - below this seen-range on any axis, the offset would be a guess, so skip correcting

// Last known-good magnetometer reading. BMM150's own output data rate is
// typically well under the ~20Hz this sketch polls at, so getMag()
// returning false (no fresh sample this tick) is the ordinary case most
// ticks, not a fault - reusing the last good reading between fresh
// samples is fine since heading changes slowly relative to this poll rate.
static float last_mag[3] = {0.0f, 0.0f, 0.0f};
static bool have_mag_reading = false;

// Updates the running min/max calibration in place and returns the
// bias-corrected reading via the same variables. Cheap enough to run every
// tick unconditionally.
void calibrateMag(float &mx, float &my, float &mz) {
  float raw[3] = {mx, my, mz};
  for (int i = 0; i < 3; i++) {
    if (raw[i] < mag_min[i]) mag_min[i] = raw[i];
    if (raw[i] > mag_max[i]) mag_max[i] = raw[i];
  }
  mx -= (mag_min[0] + mag_max[0]) * 0.5f;
  my -= (mag_min[1] + mag_max[1]) * 0.5f;
  mz -= (mag_min[2] + mag_max[2]) * 0.5f;
}

// Extracts roll/pitch (NOT yaw - see magYawCorrect below) from the current
// orientation quaternion. Standard ZYX/Tait-Bryan quaternion->Euler
// formulas - the exact decomposition of the same rotation rotateByQuat
// applies, not a separately-derived fact, so it's internally consistent
// with the rest of this file by construction rather than a second thing
// to independently get right.
void quaternionRollPitch(float &roll, float &pitch) {
  roll = atan2f(2.0f * (q0 * q1 + q2 * q3), 1.0f - 2.0f * (q1 * q1 + q2 * q2));
  float sinp = 2.0f * (q0 * q2 - q3 * q1);
  sinp = fminf(1.0f, fmaxf(-1.0f, sinp));  // clamp - float roundoff can push this just past +-1, which would NaN asinf
  pitch = asinf(sinp);
}

// Current yaw estimate straight from the orientation quaternion (gyro
// dead-reckoning, before this tick's magnetometer correction is applied) -
// same standard formula family as quaternionRollPitch.
float quaternionYaw() {
  return atan2f(2.0f * (q0 * q3 + q1 * q2), 1.0f - 2.0f * (q2 * q2 + q3 * q3));
}

float wrapToPi(float a) {
  while (a > (float)M_PI) a -= 2.0f * (float)M_PI;
  while (a < -(float)M_PI) a += 2.0f * (float)M_PI;
  return a;
}

// Applies one tick's worth of magnetometer-based yaw correction to the
// global q0..q3, if a calibrated-enough reading is available this tick.
// Safe to call unconditionally every tick - a no-op (leaves q untouched)
// on a board with no magnetometer, before calibration has seen enough
// range yet, or on the very first tick(s) before any mag reading has ever
// arrived.
void magYawCorrect(float dt) {
  float mx, my, mz;
  if (M5.Imu.getMag(&mx, &my, &mz)) {
    last_mag[0] = mx; last_mag[1] = my; last_mag[2] = mz;
    have_mag_reading = true;
  } else if (have_mag_reading) {
    mx = last_mag[0]; my = last_mag[1]; mz = last_mag[2];
  } else {
    return;  // no board magnetometer, or no reading yet at all
  }

  calibrateMag(mx, my, mz);
  if ((mag_max[0] - mag_min[0]) < MAG_CAL_MIN_RANGE ||
      (mag_max[1] - mag_min[1]) < MAG_CAL_MIN_RANGE ||
      (mag_max[2] - mag_min[2]) < MAG_CAL_MIN_RANGE) {
    return;  // not enough range seen yet on some axis - offset would be a guess, skip correcting
  }

  // Tilt-compensate: rotate the raw mag reading by a quaternion that has
  // the SAME roll/pitch as the real orientation but yaw forced to zero.
  // That isolates "how far has the field rotated relative to the body's
  // own zero-yaw reference axes" - a heading measurement in the exact same
  // reference quaternionYaw() already uses, directly comparable to it -
  // without baking in (and just circularly reconfirming) whatever yaw the
  // filter currently believes. Standard yaw=0 simplification of the usual
  // Euler->quaternion construction (the matched inverse of
  // quaternionRollPitch/quaternionYaw above, so again not a separately
  // memorised fact).
  float roll, pitch;
  quaternionRollPitch(roll, pitch);
  float cr = cosf(roll * 0.5f), sr = sinf(roll * 0.5f);
  float cp = cosf(pitch * 0.5f), sp = sinf(pitch * 0.5f);
  float tq0 = cr * cp, tq1 = sr * cp, tq2 = cr * sp, tq3 = -sr * sp;

  float lx, ly, lz;
  rotateByQuat(tq0, tq1, tq2, tq3, mx, my, mz, lx, ly, lz);
  (void)lz;  // tilt-compensated vertical component - not part of a horizontal heading
  float mag_yaw = atan2f(ly, lx);

  float err = wrapToPi(mag_yaw - quaternionYaw());
  float corr = MAG_YAW_GAIN * err * dt;
  float dq0 = cosf(corr * 0.5f), dq3 = sinf(corr * 0.5f);  // small world-frame yaw-only correction quaternion
  float nq0, nq1, nq2, nq3;
  quatMultiply(dq0, 0.0f, 0.0f, dq3, q0, q1, q2, q3, nq0, nq1, nq2, nq3);  // left-multiply: world-frame correction composed onto the existing body->world orientation
  q0 = nq0; q1 = nq1; q2 = nq2; q3 = nq3;

  float recipNorm = 1.0f / sqrtf(q0 * q0 + q1 * q1 + q2 * q2 + q3 * q3);
  q0 *= recipNorm; q1 *= recipNorm; q2 *= recipNorm; q3 *= recipNorm;
}

static unsigned long last_filter_update_ms = 0;

// Reads the IMU and returns the current shake magnitude + swing angle,
// already clamped/normalised to their respective ranges. `now` is
// millis() at call time, passed in (not read internally) so the filter's
// dt is measured against the same clock loop() already has, not a second
// independent millis() call a few instructions later.
AccelReading readAcceleration(unsigned long now) {
  float ax, ay, az, gx, gy, gz;
  M5.Imu.getAccel(&ax, &ay, &az);  // values in g; self-refreshes internally
  M5.Imu.getGyro(&gx, &gy, &gz);   // values in degrees/sec (confirmed against M5Unified's own IMU examples - if the filter tracks nonsense, check this first)

  // Real elapsed time, not the nominal send interval - ACTIVE_SEND_INTERVAL_MS
  // and IDLE_SEND_INTERVAL_MS aren't the same rate, and neither is exact
  // tick to tick. First call after boot has no prior sample to diff
  // against - dt=0 skips integration for just that one call rather than
  // integrating over a bogus huge (or negative, around a millis() rollover)
  // gap.
  float dt = (last_filter_update_ms == 0) ? 0.0f : (now - last_filter_update_ms) / 1000.0f;
  last_filter_update_ms = now;
  if (dt > 0.0f) {
    madgwickUpdateIMU(gx * DEG_TO_RAD_F, gy * DEG_TO_RAD_F, gz * DEG_TO_RAD_F, ax, ay, az, dt);
    if (MAG_YAW_CORRECTION_ENABLED) magYawCorrect(dt);  // see MAG_YAW_CORRECTION_ENABLED's own comment for why this is off
  }

  float magnitude = sqrtf(ax * ax + ay * ay + az * az);
  float acceleration = fabsf(magnitude - BASELINE_G) * SENSITIVITY;
  if (acceleration > 1.0f) acceleration = 1.0f;
  if (acceleration < 0.0f) acceleration = 0.0f;

  // Rotate the raw (gravity + swing) reading into the world frame - unlike
  // the old body-frame version, this stays meaningful however the stick is
  // currently tilted/rotated in hand, since the filter above is tracking
  // that rotation continuously. atan2(y, x) matches the old version's axis
  // order, just over world-frame components now instead of body-frame
  // ones. (Gravity itself doesn't need subtracting out first: it's a
  // constant offset along world Z by construction of this filter, and
  // atan2 over the X/Y plane is blind to Z entirely.)
  float wx, wy, wz;
  rotateBodyToWorld(ax, ay, az, wx, wy, wz);
  (void)wz;  // world-Z (gravity + any vertical swing component) isn't part of this horizontal-plane angle
  float angle_deg = atan2f(wy, wx) * (180.0f / (float)M_PI);
  if (angle_deg < 0.0f) angle_deg += 360.0f;

  return {acceleration, angle_deg};
}

void sendReading(float acceleration, float angle_deg, int battery_pct) {
  if (battery_pct >= 0) {
    Serial.printf("{\"acceleration\": %.3f, \"angle_deg\": %.1f, \"battery_pct\": %d}\n", acceleration, angle_deg, battery_pct);
  } else {
    // Unknown/unavailable battery reading (e.g. running off USB with no
    // cell fitted) - omit the field entirely rather than send a fake -1,
    // same "only include a key when it's actually valid" pattern
    // heart_rate.py's spo2 field uses on the Pi side.
    Serial.printf("{\"acceleration\": %.3f, \"angle_deg\": %.1f}\n", acceleration, angle_deg);
  }
}

// ---------------------------------------------------------------------------
// 2. Battery level reporting
// ---------------------------------------------------------------------------
// Polled far less often than acceleration - the battery percentage doesn't
// change meaningfully at 20Hz, and M5.Power's fuel-gauge read is a slower
// I2C round-trip than the IMU's, no reason to pay that cost every tick.

static constexpr unsigned long BATTERY_POLL_INTERVAL_MS = 5000;
static int cached_battery_pct = -1;
static unsigned long last_battery_poll = 0;

void updateBatteryReading(unsigned long now) {
  if (now - last_battery_poll < BATTERY_POLL_INTERVAL_MS) return;
  last_battery_poll = now;

  int32_t level = M5.Power.getBatteryLevel();  // 0-100, or a negative sentinel if unknown
  cached_battery_pct = (level >= 0 && level <= 100) ? (int)level : -1;
}

// ---------------------------------------------------------------------------
// 3. On-device screen feedback
// ---------------------------------------------------------------------------
// Was a bottom-up magnitude bar - dropped (2026-08-01, didn't look nice) in
// favour of an idle invite instead: "SWING ME" while the stick's resting,
// clearing to blank the moment real motion is detected, so the prompt
// doesn't clutter the screen while someone's actually using it. Skipped
// entirely while the display is asleep (section 4) - drawing to a sleeping
// panel wastes power for nothing visible.
//
// Own thresholds rather than reusing section 4's IDLE_THRESHOLD - this file's
// top-of-file comment deliberately keeps each of these 4 sections pullable
// without touching the others, so this stays self-contained even though the
// values happen to be in the same ballpark as section 4's today.
//
// Two thresholds with a dead zone between them (hysteresis), not one -
// a single fixed cutoff checked fresh every ~50ms frame means any tiny
// jitter straddling that one line (an ordinary hand tremor while just
// holding it, not an actual swing) flips the text on/off every frame,
// which reads as the screen glitching (real complaint, 2026-08-17: "a
// tiny little movement is sometimes detected as moving"). Requiring a
// clearly-bigger acceleration to dismiss it than to bring it back means a
// borderline reading just holds whatever state it was already in instead
// of flapping - same "don't snap-react to one borderline reading" idea as
// the arm-glide effect's IDLE_RESET_SECONDS on the Pi side. Untuned
// placeholders like every other threshold in this file (see class-level
// SENSITIVITY comment) - raise SWING_INVITE_HIDE_ABOVE further if ordinary
// handling still dismisses it too easily on real hardware.
static const float SWING_INVITE_SHOW_BELOW = 0.05f;  // calm enough to bring the invite back
static const float SWING_INVITE_HIDE_ABOVE = 0.15f;  // a real swing, not just jitter - dismisses it

static bool swing_invite_visible = true;

void updateScreen(float acceleration, bool display_asleep) {
  if (display_asleep) return;

  if (acceleration > SWING_INVITE_HIDE_ABOVE) {
    swing_invite_visible = false;
  } else if (acceleration < SWING_INVITE_SHOW_BELOW) {
    swing_invite_visible = true;
  }
  // else: acceleration is in the dead zone between the two thresholds -
  // leave swing_invite_visible exactly as it was, on purpose.

  auto &dsp = M5.Display;
  dsp.startWrite();
  dsp.fillScreen(TFT_BLACK);
  if (swing_invite_visible) {
    // setTextDatum(middle_center) + drawString at the screen's centre point
    // is the standard LovyanGFX/M5GFX centred-text pattern - not compile-
    // verified against this board's exact library version yet, check this
    // renders correctly (position/size) the first time you flash it.
    dsp.setTextColor(TFT_CYAN, TFT_BLACK);
    dsp.setTextSize(2);
    dsp.setTextDatum(middle_center);
    dsp.drawString("SHAKE ME", dsp.width() / 2, dsp.height() / 2);
  }
  dsp.endWrite();
}

// ---------------------------------------------------------------------------
// 4. Idle power save
// ---------------------------------------------------------------------------
// NOT true deep-sleep-until-shaken - that would need the IMU's motion-
// interrupt pin wired to an ESP32 wake pin, which needs this exact board's
// schematic to confirm safely (not verified here, no hardware in hand to
// test against). What this DOES do, safely and verifiably via M5Unified's
// own API: after a stretch of no meaningful motion, turn the screen (the
// single biggest power draw on this board while idle) off, and slow the
// send/sample rate down. Both reverse instantly on the next real shake.

static constexpr float IDLE_THRESHOLD = 0.05f;          // below this counts as "not being handled"
static constexpr unsigned long IDLE_TIMEOUT_MS = 30000; // how long idle before the screen sleeps
static constexpr unsigned long IDLE_SEND_INTERVAL_MS = 500; // slower send/sample rate once idle

static bool display_asleep = false;
static unsigned long last_active_at = 0;

// Returns the send interval that should be used right now, and updates
// display sleep/wake state as a side effect - kept together since both are
// driven by the same "how long has it been idle" clock.
unsigned long updateIdlePowerSave(float acceleration, unsigned long now) {
  if (acceleration > IDLE_THRESHOLD) {
    last_active_at = now;
    if (display_asleep) {
      M5.Display.wakeup();
      display_asleep = false;
    }
    return ACTIVE_SEND_INTERVAL_MS;
  }

  if (!display_asleep && (now - last_active_at > IDLE_TIMEOUT_MS)) {
    M5.Display.sleep();  // panel + backlight off
    display_asleep = true;
  }
  return display_asleep ? IDLE_SEND_INTERVAL_MS : ACTIVE_SEND_INTERVAL_MS;
}

// ---------------------------------------------------------------------------
// setup / loop
// ---------------------------------------------------------------------------

void setup() {
  auto cfg = M5.config();
  M5.begin(cfg);
  Serial.begin(115200);

  if (M5.Imu.getType() == m5::imu_none) {
    // No IMU detected on this board - nothing sensible to report. Loop
    // forever rather than print garbage; accel_stick.py falls back to its
    // own mock if the Pi never sees a line from this port at all.
    for (;;) {
      delay(1000);
    }
  }

  last_active_at = millis();
}

void loop() {
  static unsigned long last_send = 0;
  unsigned long now = millis();
  unsigned long send_interval = display_asleep ? IDLE_SEND_INTERVAL_MS : ACTIVE_SEND_INTERVAL_MS;

  if (now - last_send >= send_interval) {
    last_send = now;

    AccelReading reading = readAcceleration(now);
    updateBatteryReading(now);
    send_interval = updateIdlePowerSave(reading.acceleration, now);  // may flip display_asleep for next loop
    updateScreen(reading.acceleration, display_asleep);
    sendReading(reading.acceleration, reading.angle_deg, cached_battery_pct);
  }

  M5.update();  // keeps M5Unified's internal button/touch state fresh
}
