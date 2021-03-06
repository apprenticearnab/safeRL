'''
Parallel implementation of the Augmented Random Search method.
Horia Mania --- hmania@berkeley.edu
Aurelia Guy
Benjamin Recht 
'''

import parser
import time
import os
import numpy as np
import gym
import logz
import ray
import utils
import optimizers
from policies_safe import *
import socket
from shared_noise import *
import MADRaS
import sys
#f = open('logs_1.txt', 'w')
#sys.stdout = f
import sys
import copy
from collections import namedtuple
from itertools import count
from PIL import Image
import torch
import torch.nn as nn
from torch.nn import Linear, Module, MSELoss

import torch.optim as optim
import torch.nn.functional as F
import torchvision.transforms as T
import math
import random
os.environ["CUDA_VISIBLE_DEVICES"] = '0'


Transition = namedtuple('Transition',('state', 'action', 'next_state', 'reward'))

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
class ReplayMemory(object):

    def __init__(self, capacity):
        self.capacity = capacity
        self.memory = []
        self.position = 0

    def push(self, *args):
        """Saves a transition."""
        if len(self.memory) < self.capacity:
            self.memory.append(None)
        self.memory[self.position] = Transition(*args)
        self.position = (self.position + 1) % self.capacity

    def sample(self, batch_size):
        return random.sample(self.memory, batch_size)

    def __len__(self):
        return len(self.memory)



@ray.remote
class Worker(object):
    """ 
    Object class for parallel rollout generation.
    """

    def __init__(self, env_seed,
                 env_name='',
                 policy_params = None,
                 deltas=None,
                 rollout_length=1000,
                 delta_std=0.02):

 
        #sys.path.append('/home/harshit/work/')
        #import MADRaS
 
        # initialize OpenAI environment for each worker
        
        # logging.warning('Env start')
        sys.path.append('/home/harshit/work')
        import MADRaS
        self.env_name = env_name
        self.env = gym.make(env_name)
        self.env.seed(env_seed)

        # each worker gets access to the shared noise table
        # with independent random streams for sampling
        # from the shared noise table. 
        self.deltas = SharedNoiseTable(deltas, env_seed + 7)
        self.policy_params = policy_params
        if policy_params['type'] == 'linear':
            self.policy = LinearPolicy(policy_params)

        elif policy_params['type'] == 'bilayer':
            self.policy = BilayerPolicy(policy_params)
        elif policy_params['type'] == 'bilayer_safe_explorer':
            self.policy = SafeBilayerExplorerPolicy(policy_params,trained_weights='/home/harshit/work/ARS/trained_policies/Madras-explore7/safeQ_torch119.pt')

        else:
            raise NotImplementedError
            
        self.delta_std = delta_std
        self.rollout_length = rollout_length

    def __str__(self):
        return "Env_NAME:{} policy_params:{}".format(self.env_name,self.policy_params)
    def __repr__(self):
        return "Env_NAME:{} policy_params:{}".format(self.env_name,self.policy_params)
       

    def get_weights_plus_stats(self):
        """ 
        Get current policy weights and current statistics of past states.
        """
        assert (self.policy_params['type'] == 'bilayer' or self.policy_params['type'] == 'linear' or self.policy_params['type'] == 'bilayer_safe_explorer')
        return self.policy.get_weights_plus_stats()
    

    def rollout(self, shift = 0., rollout_length = None):
        """ 
        Performs one rollout of maximum length rollout_length. 
        At each time-step it substracts shift from the reward.
        """
        
        if rollout_length is None:
            rollout_length = self.rollout_length

        total_reward = 0.
        steps = 0
        my_f= open('Violations.txt','a')
        transitions = []
        record_transitions = True
        cost = 0
        ob = self.env.reset()
        for i in range(rollout_length):
            #my_f.write("{} \n".format(ob[20]))

            weights = self.policy.getQ(ob)
            action = self.policy.act(ob)
            C = 0.8
            # Solve the lagrangian
            lagrangian = max(float(np.sum(weights*action) + ob[20] -C)/(np.sum(weights**2)),0)
            #my_f.write("lagrangian: {} \n".format(lagrangian))
            a_star = action - lagrangian*weights
            next_ob, reward, done, _ = self.env.step(a_star)
            cost=float(np.sum(weights*action)) + ob[20]
            if(ob[20]>1):
                my_f.write("Violated: \n")
                my_f.write("Obs: {} \n".format(ob))        
                my_f.write("action given: {} \n".format(action))     
                my_f.write("action taken: {} \n".format(a_star))
                my_f.write("cost: {} \n".format(cost))
                my_f.write("weights: {} \n".format(weights))     

                my_f.write("---------------\n")

            steps+= 1
            total_reward += (reward - shift)
            ob = next_ob
            if done:
                break





            # action = self.policy.act(ob)
            # next_ob, reward, done, _ = self.env.step(action)
            # if record_transitions==True:
            #     transitions.append([ob,action,reward,next_ob])

            # # Constraints for linear safety layer
            # if(next_ob[20]<-0.8 or next_ob[20]>0.8 ):
            #     record_transitions=False                
 


            # steps += 1
            # total_reward += (reward - shift)
            # ob=next_ob
            # if done:
            #     break
            
        return total_reward, steps, transitions

    def linesearch(self, delta, backtrack_ratio=0.5, num_backtracks=10):
        deltas = [delta]
        # for i in range(num_backtracks/2):
        #     deltas.append(delta*(backtrack_ratio)**i)
        #     deltas.insert(0,delta/(backtrack_ratio)**i)
        return deltas


    def do_rollouts(self, w_policy, num_rollouts = 1, shift = 1, evaluate = False):
        """ 
        Generate multiple rollouts with a policy parametrized by w_policy.
        """
        all_transitions = []
        rollout_rewards, deltas_idx = [], []
        steps = 0

        for i in range(num_rollouts):

            if evaluate:
                print("EVAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAL")
                self.policy.update_weights(w_policy)
                deltas_idx.append(-1)
                
                # set to false so that evaluation rollouts are not used for updating state statistics
                self.policy.update_filter = False

                # for evaluation we do not shift the rewards (shift = 0) and we use the
                # default rollout length (1000 for the MuJoCo locomotion tasks)
                reward, r_steps, transitions = self.rollout(shift = 0., rollout_length = self.env.spec.timestep_limit)
                rollout_rewards.append(reward)
                
            else:
                idx, delta = self.deltas.get_delta(w_policy.size)
             
                delta = (self.delta_std * delta).reshape(w_policy.shape)
                deltas_idx.append(idx)

                # set to true so that state statistics are updated 
                self.policy.update_filter = True

                # compute reward and number of timesteps used for positive perturbation rollout
                self.policy.update_weights(w_policy + delta)
                pos_reward, pos_steps, transitions  = self.rollout(shift = shift)
                all_transitions = all_transitions+transitions


                # compute reward and number of timesteps used for negative pertubation rollout
                self.policy.update_weights(w_policy - delta)
                neg_reward, neg_steps, transitions = self.rollout(shift = shift) 
                steps += pos_steps + neg_steps
                all_transitions = all_transitions+transitions
                rollout_rewards.append([pos_reward, neg_reward])
                            
        return {'deltas_idx': deltas_idx, 'rollout_rewards': rollout_rewards, "steps" : steps, "transitions":all_transitions}
    
    def stats_increment(self):
        self.policy.observation_filter.stats_increment()
        return

    def get_weights(self):
        return self.policy.get_weights()
    
    def get_filter(self):
        return self.policy.observation_filter

    def sync_filter(self, other):
        self.policy.observation_filter.sync(other)
        return

    
class ARSLearner(object):
    """ 
    Object class implementing the ARS algorithm.
    """

    def __init__(self, env_name='HalfCheetah-v1',
                 policy_params=None,
                 num_workers=32, 
                 num_deltas=320, 
                 deltas_used=320,
                 delta_std=0.02, 
                 logdir=None, 
                 rollout_length=1000,
                 step_size=0.01,
                 shift='constant zero',
                 params=None,
                 seed=123):
        
        logz.configure_output_dir(logdir)
        logz.save_params(params)
 
        env = gym.make(env_name)
       
        self.timesteps = 0
        self.action_size = env.action_space.shape[0]
        self.ob_size = env.observation_space.shape[0]
        self.num_deltas = num_deltas
        self.deltas_used = deltas_used
        self.rollout_length = rollout_length
        self.step_size = step_size
        self.delta_std = delta_std
        self.logdir = logdir
        self.shift = shift
        self.params = params
        self.max_past_avg_reward = float('-inf')
        self.num_episodes_used = float('inf')

        # Parameters for Q Learner
        self.memory = ReplayMemory(10000)
        self.BATCH_SIZE = 128
        self.GAMMA = 0.999
        self.TARGET_UPDATE = 5



        
        # create shared table for storing noise
        print("Creating deltas table.")
        deltas_id = create_shared_noise.remote()
        self.deltas = SharedNoiseTable(ray.get(deltas_id), seed = seed + 3)
        print('Created deltas table.')

        # initialize workers with different random seeds
        print('Initializing workers.') 
        self.num_workers = num_workers
        self.workers = [Worker.remote(seed + 7 * i,
                                      env_name=env_name,
                                      policy_params=policy_params,
                                      deltas=deltas_id,
                                      rollout_length=rollout_length,
                                      delta_std=delta_std) for i in range(num_workers)]

        print(self.workers[0])
        # initialize policy 
        if policy_params['type'] == 'linear':
            self.policy = LinearPolicy(policy_params)
            self.w_policy = self.policy.get_weights()
        elif policy_params['type'] == 'bilayer':
            self.policy = BilayerPolicy(policy_params)
            self.w_policy = self.policy.get_weights()
        elif policy_params['type'] == 'bilayer_safe_explorer':
            self.policy = SafeBilayerExplorerPolicy(policy_params,trained_weights='/home/harshit/work/ARS/trained_policies/Madras-explore7/safeQ_torch119.pt')
            self.w_policy = self.policy.get_weights()
        else:
            raise NotImplementedError
            
        # initialize optimization algorithm
        self.optimizer = optimizers.SGD(self.w_policy, self.step_size)        
        print("Initialization of ARS complete.")

    def aggregate_rollouts(self, num_rollouts = None, evaluate = False):
        """ 
        Aggregate update step from rollouts generated in parallel.
        """

        if num_rollouts is None:
            num_deltas = self.num_deltas
            #print("TRAIN")
        else:
            num_deltas = num_rollouts
            #print("TEST")
            
        # put policy weights in the object store
        policy_id = ray.put(self.w_policy)

        t1 = time.time()
        num_rollouts = int(num_deltas / self.num_workers)
        #print("NUM_ROLLOUTS {}".format(num_rollouts))
            
        # parallel generation of rollouts
        rollout_ids_one = [worker.do_rollouts.remote(policy_id,
                                                 num_rollouts = num_rollouts,
                                                 shift = self.shift,
                                                 evaluate=evaluate) for worker in self.workers]

        rollout_ids_two = [worker.do_rollouts.remote(policy_id,
                                                 num_rollouts = 1,
                                                 shift = self.shift,
                                                 evaluate=evaluate) for worker in self.workers[:(num_deltas % self.num_workers)]]

        # gather results 
        results_one = ray.get(rollout_ids_one)
        results_two = ray.get(rollout_ids_two)

        rollout_rewards, deltas_idx = [], [] 
        all_transitions = []

        for result in results_one:
            if not evaluate:
                self.timesteps += result["steps"]
            deltas_idx += result['deltas_idx']
            rollout_rewards += result['rollout_rewards']
            all_transitions+=result['transitions']


        for result in results_two:
            if not evaluate:
                self.timesteps += result["steps"]
            deltas_idx += result['deltas_idx']
            rollout_rewards += result['rollout_rewards']
            all_transitions+=result['transitions']

        deltas_idx = np.array(deltas_idx)
        rollout_rewards = np.array(rollout_rewards, dtype = np.float64)
        

        # Push all the transitions collected in the Replay Buffer
        for tran in all_transitions:
            self.memory.push(torch.from_numpy(tran[0]).unsqueeze(0).to(device).float(),torch.tensor([[tran[1]]],device=device, dtype=torch.long),torch.from_numpy(tran[3]).unsqueeze(0).float().to(device),torch.tensor([tran[2]],device=device))


        print('Maximum reward of collected rollouts:', rollout_rewards.max())
        t2 = time.time()

        print('Time to generate rollouts:', t2 - t1)

        if evaluate:
            return rollout_rewards

        # select top performing directions if deltas_used < num_deltas
        max_rewards = np.max(rollout_rewards, axis = 1)
        if self.deltas_used > self.num_deltas:
            self.deltas_used = self.num_deltas
            
        idx = np.arange(max_rewards.size)[max_rewards >= np.percentile(max_rewards, 100*(1 - (self.deltas_used / self.num_deltas)))]
        deltas_idx = deltas_idx[idx]
        rollout_rewards = rollout_rewards[idx,:]
        
        # normalize rewards by their standard deviation
        if np.std(rollout_rewards)!=0:
            rollout_rewards /= np.std(rollout_rewards)

        t1 = time.time()
        # aggregate rollouts to form g_hat, the gradient used to compute SGD step
        g_hat, count = utils.batched_weighted_sum(rollout_rewards[:,0] - rollout_rewards[:,1],
                                                  (self.deltas.get(idx, self.w_policy.size)
                                                   for idx in deltas_idx),
                                                  batch_size = 500)
        g_hat /= deltas_idx.size
        t2 = time.time()
        print('time to aggregate rollouts', t2 - t1)
        return g_hat
        

    def train_step(self):
        """ 
        Perform one update step of the policy weights.
        """
        
        g_hat = self.aggregate_rollouts()                    
        print("Euclidean norm of update step:", np.linalg.norm(g_hat))
        self.w_policy -= self.optimizer._compute_step(g_hat).reshape(self.w_policy.shape)
        self.policy.update_weights(self.w_policy)
        return

    def update_explorer_net(self):
        if len(self.memory) < self.BATCH_SIZE:
            return


        transitions = self.memory.sample(self.BATCH_SIZE)
        batch = Transition(*zip(*transitions))

        state_batch = torch.cat(batch.state)
        action_batch = torch.cat(batch.action)


        # Convert to numpy arrays
        state_np = np.asarray([i.cpu().numpy() for i in batch.state])
        action_np = np.asarray([i.cpu().numpy().astype(np.float64) for i in batch.action])
        next_state_np = np.asarray([i.cpu().numpy() for i in batch.next_state])


        # set up the costs for constraints
        next_state_np = next_state_np.reshape(next_state_np.shape[0],-1)
        cost_next_state = np.asarray([100 if i[20]<=-0.8 or i[20]>=0.8  else 0 for i in next_state_np])
        state_np = state_np.reshape(state_np.shape[0],-1)
        action_np = action_np.reshape(action_np.shape[0],-1)
        cost_state = np.asarray([100 if i[20]<=-0.8 or i[20]>=0.8  else 0 for i in state_np])

        transpose_action = self.policy.safeQ(state_batch)

        mul = torch.mul(transpose_action,torch.from_numpy(action_np).to(device).float())
        mul = torch.sum(mul,dim=1)
        # print(mul.size())
        # print(torch.from_numpy(cost_state).to(device).float().size())
        target = torch.from_numpy(cost_state).to(device).float()+mul
        # print(target.size())
        # print(target.view(1,-1).size())
        # print(torch.from_numpy(cost_next_state).to(device).float().size())
        loss = F.mse_loss(torch.from_numpy(cost_next_state).to(device).float(), target.view(1,-1))
        # Optimize the model
        self.policy.optimizer.zero_grad()
        loss.backward()
        # for param in self.policy.safeQ.parameters():
        #     param.grad.data.clamp_(-1, 1)
        self.policy.optimizer.step()


    def train(self, num_iter):
        max_reward_ever=-1
        start = time.time()
        for i in range(num_iter):
            
            t1 = time.time()
            self.train_step()
            #for iter_ in range(10):
                #self.update_explorer_net()
            t2 = time.time()
            print('total time of one step', t2 - t1)           
            print('iter ', i,' done')
            # if i == num_iter-1:
            #     np.savez(self.logdir + "/lin_policy_plus" + str(i), w) 
            # record statistics every 10 iterations
            if ((i + 1) % 20 == 0):
                



                rewards = self.aggregate_rollouts(num_rollouts = 30, evaluate = True)
                print("SHAPE",rewards.shape)
                if(np.mean(rewards)>max_reward_ever):
                    max_reward_ever=np.mean(rewards)
                #     np.savez(self.logdir + "/lin_policy_plus", w)

                w = ray.get(self.workers[0].get_weights_plus_stats.remote())

                np.savez(self.logdir + "/bi_policy_num_plus" + str(i), w)
                torch.save(self.policy.net.state_dict(),self.logdir + "/bi_policy_num_plus_torch" + str(i)+ ".pt")
                torch.save(self.policy.safeQ.state_dict(),self.logdir + "/safeQ_torch" + str(i)+ ".pt")


                # np.savez(self.logdir + "/bi_policy_num_plus" + str(i), w)
                # torch.save(self.policy.net.state_dict(),self.logdir + "/bi_policy_num_plus_torch" + str(i)+ ".pt")
                print(sorted(self.params.items()))
                logz.log_tabular("Time", time.time() - start)
                logz.log_tabular("Iteration", i + 1)
                logz.log_tabular("BestRewardEver", max_reward_ever)
                logz.log_tabular("AverageReward", np.mean(rewards))
                logz.log_tabular("StdRewards", np.std(rewards))
                logz.log_tabular("MaxRewardRollout", np.max(rewards))
                logz.log_tabular("MinRewardRollout", np.min(rewards))
                logz.log_tabular("timesteps", self.timesteps)
                logz.dump_tabular()
                
            t1 = time.time()
            # get statistics from all workers
            for j in range(self.num_workers):
                self.policy.observation_filter.update(ray.get(self.workers[j].get_filter.remote()))
            self.policy.observation_filter.stats_increment()

            # make sure master filter buffer is clear
            self.policy.observation_filter.clear_buffer()
            # sync all workers
            filter_id = ray.put(self.policy.observation_filter)
            setting_filters_ids = [worker.sync_filter.remote(filter_id) for worker in self.workers]
            # waiting for sync of all workers
            ray.get(setting_filters_ids)
         
            increment_filters_ids = [worker.stats_increment.remote() for worker in self.workers]
            # waiting for increment of all workers
            ray.get(increment_filters_ids)            
            t2 = time.time()
            print('Time to sync statistics:', t2 - t1)
                        
        return 

def run_ars(params):

    dir_path = params['dir_path']

    if not(os.path.exists(dir_path)):
        os.makedirs(dir_path)
    logdir = dir_path
    if not(os.path.exists(logdir)):
        os.makedirs(logdir)

    env = gym.make(params['env_name'])
    ob_dim = env.observation_space.shape[0]
    ac_dim = env.action_space.shape[0]

    # set policy parameters. Possible filters: 'MeanStdFilter' for v2, 'NoFilter' for v1.
    policy_params={'type':'bilayer_safe_explorer',
                   'ob_filter':params['filter'],
                   'ob_dim':ob_dim,
                   'ac_dim':ac_dim}

    ARS = ARSLearner(env_name=params['env_name'],
                     policy_params=policy_params,
                     num_workers=params['n_workers'], 
                     num_deltas=params['n_directions'],
                     deltas_used=params['deltas_used'],
                     step_size=params['step_size'],
                     delta_std=params['delta_std'], 
                     logdir=logdir,
                     rollout_length=params['rollout_length'],
                     shift=params['shift'],
                     params=params,
                     seed = params['seed'])
        
    ARS.train(params['n_iter'])
       
    return 


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--env_name', type=str, default='Madras-v0')
    parser.add_argument('--n_iter', '-n', type=int, default=1000)
    parser.add_argument('--n_directions', '-nd', type=int, default=8)
    parser.add_argument('--deltas_used', '-du', type=int, default=8)
    parser.add_argument('--step_size', '-s', type=float, default=0.02)
    parser.add_argument('--delta_std', '-std', type=float, default=.03)
    parser.add_argument('--n_workers', '-e', type=int, default=6)
    parser.add_argument('--rollout_length', '-r', type=int, default=5000)

    # for Swimmer-v1 and HalfCheetah-v1 use shift = 0
    # for Hopper-v1, Walker2d-v1, and Ant-v1 use shift = 1
    # for Humanoid-v1 used shift = 5
    parser.add_argument('--shift', type=float, default=1)
    parser.add_argument('--seed', type=int, default=237)
    parser.add_argument('--policy_type', type=str, default='linear')
    parser.add_argument('--dir_path', type=str, default='trained_policies/Madras-explore8')
    parser.add_argument('--logdir', type=str, default='trained_policies/Madras-explore8')

    # for ARS V1 use filter = 'NoFilter'
    parser.add_argument('--filter', type=str, default='MeanStdFilter')

    local_ip = socket.gethostbyname(socket.gethostname())
    ray.init(redis_address="10.32.6.37:6382")
    
    args = parser.parse_args()
    params = vars(args)
    run_ars(params)

